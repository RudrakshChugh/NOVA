"""Logistic-regression combiner over the four signals (plan §3.1).

Logistic regression is chosen over a learned ensemble because the per-signal weight is directly
reportable, it is stable at the sample sizes this project has, and it is fast enough to run inside
the generation loop. The fitted model is four coefficients, an intercept and a threshold, so it is
stored as JSON: diffable, reviewable, and impossible to confuse with an opaque artifact.

Protocol (plan §5): fit on the train fold only; select the regularisation strength and the
decision threshold on the validation fold only; touch the test fold exactly once, through
`evaluate()`, for the numbers that go in the report.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..metrics import bootstrap_ci, confusion, f1, precision, recall
from .signals import SIGNAL_NAMES

# Fallback used only when no combiner has been fitted yet. Named in every report it produces so a
# result can never be mistaken for one from a fitted model.
UNTRAINED_VERSION = "untrained:equal-weight-mean"
UNTRAINED_THRESHOLD = 0.85

C_GRID = (0.01, 0.1, 1.0, 10.0, 100.0)


@dataclass
class NoveltyCombiner:
    coefficients: dict[str, float]
    intercept: float
    threshold: float
    version: str
    trained_at: str | None = None
    metrics: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)

    @property
    def is_fitted(self) -> bool:
        return self.version != UNTRAINED_VERSION

    @classmethod
    def untrained(cls) -> "NoveltyCombiner":
        """Equal-weight mean of the four signals. A stand-in so the audit can run before the
        labelled pair set exists — not a fitted model, and labelled as such."""
        return cls(
            coefficients={name: 1.0 / len(SIGNAL_NAMES) for name in SIGNAL_NAMES},
            intercept=0.0,
            threshold=UNTRAINED_THRESHOLD,
            version=UNTRAINED_VERSION,
        )

    def probability(self, signals: dict[str, float]) -> float:
        """Duplicate probability for one signal vector."""
        if not self.is_fitted:
            return sum(self.coefficients[n] * signals.get(n, 0.0) for n in SIGNAL_NAMES)
        z = self.intercept + sum(self.coefficients[n] * signals.get(n, 0.0) for n in SIGNAL_NAMES)
        return 1.0 / (1.0 + pow(2.718281828459045, -z))

    def is_duplicate(self, signals: dict[str, float]) -> bool:
        return self.probability(signals) >= self.threshold

    # --- persistence ---

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "trained_at": self.trained_at,
            "signal_names": list(SIGNAL_NAMES),
            "coefficients": self.coefficients,
            "intercept": self.intercept,
            "threshold": self.threshold,
            "params": self.params,
            "metrics": self.metrics,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "NoveltyCombiner":
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            coefficients=d["coefficients"], intercept=d["intercept"], threshold=d["threshold"],
            version=d["version"], trained_at=d.get("trained_at"), metrics=d.get("metrics", {}),
            params=d.get("params", {}),
        )


Sample = tuple[dict[str, float], bool]  # (signal vector, is_duplicate)


def _matrix(samples: Sequence[Sample]):
    return (
        [[s.get(n, 0.0) for n in SIGNAL_NAMES] for s, _ in samples],
        [1 if label else 0 for _, label in samples],
    )


def _best_threshold(probabilities: Sequence[float], labels: Sequence[bool]) -> tuple[float, float]:
    """Threshold maximising F1 on the validation fold. Ties are broken toward higher precision:
    a false positive blocks a genuinely novel question, which costs more than a missed duplicate
    that a reviewer may still catch."""
    candidates = sorted({round(p, 4) for p in probabilities} | {0.5})
    best = (0.5, -1.0, -1.0)
    for t in candidates:
        pairs = [(bool(y), p >= t) for p, y in zip(probabilities, labels)]
        score = f1(pairs)
        if score is None:
            continue
        prec = precision(pairs) or 0.0
        if (score, prec) > (best[1], best[2]):
            best = (t, score, prec)
    return best[0], best[1]


def fit(train: Sequence[Sample], validation: Sequence[Sample], version: int) -> NoveltyCombiner:
    """Fit on train, select C and the threshold on validation. The test fold is not touched."""
    from sklearn.linear_model import LogisticRegression

    if not train or not validation:
        raise ValueError("fitting needs a non-empty train and validation fold")

    x_train, y_train = _matrix(train)
    x_val, y_val = _matrix(validation)

    # C_GRID runs from most to least regularised and the comparison is strictly greater, so a tie
    # on validation F1 keeps the simplest model. At small sample sizes every C often ties, which
    # means the selection is uninformative rather than wrong — the model card reports the C chosen
    # and the fold sizes so a reader can see when that is the case.
    best = None
    for c in C_GRID:
        model = LogisticRegression(C=c, class_weight="balanced", max_iter=1000)
        model.fit(x_train, y_train)
        probs = [p[1] for p in model.predict_proba(x_val)]
        threshold, score = _best_threshold(probs, y_val)
        if best is None or score > best[0]:
            best = (score, c, threshold, model)

    val_f1, c, threshold, model = best
    coefficients = {name: float(w) for name, w in zip(SIGNAL_NAMES, model.coef_[0])}
    return NoveltyCombiner(
        coefficients=coefficients,
        intercept=float(model.intercept_[0]),
        threshold=float(threshold),
        version=f"logistic_regression:v{version}",
        trained_at=datetime.now(timezone.utc).isoformat(),
        params={"C": c, "class_weight": "balanced", "selected_on": "validation fold"},
        metrics={"validation_f1": val_f1, "n_train": len(train), "n_validation": len(validation)},
    )


def evaluate(combiner: NoveltyCombiner, test: Sequence[Sample]) -> dict:
    """Held-out performance with bootstrap intervals. Call once, for the reported numbers."""
    pairs = [(label, combiner.is_duplicate(signals)) for signals, label in test]
    return {
        "n_test": len(test),
        "threshold": combiner.threshold,
        "confusion": confusion(pairs),
        "precision": bootstrap_ci(pairs, precision).as_dict(),
        "recall": bootstrap_ci(pairs, recall).as_dict(),
        "f1": bootstrap_ci(pairs, f1).as_dict(),
    }


def single_signal_baseline(test: Sequence[Sample], signal: str, threshold: float) -> dict:
    """The baseline plan §3.1 says a multi-signal engine must beat: one embedding threshold."""
    pairs = [(label, signals.get(signal, 0.0) >= threshold) for signals, label in test]
    return {
        "signal": signal,
        "threshold": threshold,
        "confusion": confusion(pairs),
        "precision": bootstrap_ci(pairs, precision).as_dict(),
        "recall": bootstrap_ci(pairs, recall).as_dict(),
        "f1": bootstrap_ci(pairs, f1).as_dict(),
    }
