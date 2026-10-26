"""The ranker and the two floors it must beat (plan §3.1).

The claim the ranker makes is about *order*: that it puts the questions a reviewer would accept
near the top. So the three models here are compared on NDCG@k over held-out review sittings, not
on classification accuracy.

  lightgbm            gradient-boosted trees over the feature set
  logistic_regression the same features, a linear floor
  compliance_only     a single feature, the blueprint satisfaction rate

If LightGBM does not beat both floors, the simpler model ships and that result is reported.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence

from .features import FEATURE_NAMES, vector

COMPLIANCE_ONLY = "compliance_only"
LOGISTIC_REGRESSION = "logistic_regression"
LIGHTGBM = "lightgbm"


@dataclass
class Example:
    features: dict[str, float]
    accepted: bool
    relevance: int
    session: tuple


class Scorer(Protocol):
    algorithm: str

    def score(self, features: dict[str, float]) -> float: ...


@dataclass
class ComplianceOnlyRanker:
    """The floor that uses no learning at all: order by blueprint satisfaction rate."""

    algorithm: str = COMPLIANCE_ONLY

    def score(self, features: dict[str, float]) -> float:
        return features.get("satisfaction_rate", 0.0)

    def to_dict(self) -> dict:
        return {"algorithm": self.algorithm}


@dataclass
class LinearRanker:
    """Logistic regression over the full feature set. Stored as JSON so its weights are readable."""

    coefficients: dict[str, float]
    intercept: float
    algorithm: str = LOGISTIC_REGRESSION
    params: dict = field(default_factory=dict)

    def score(self, features: dict[str, float]) -> float:
        z = self.intercept + sum(self.coefficients.get(n, 0.0) * features.get(n, 0.0) for n in FEATURE_NAMES)
        return 1.0 / (1.0 + pow(2.718281828459045, -z))

    def to_dict(self) -> dict:
        return {
            "algorithm": self.algorithm, "coefficients": self.coefficients,
            "intercept": self.intercept, "params": self.params,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LinearRanker":
        return cls(coefficients=d["coefficients"], intercept=d["intercept"], params=d.get("params", {}))


@dataclass
class BoostedRanker:
    """LightGBM. The booster is kept in its own text format, which stays reviewable."""

    booster_text: str
    algorithm: str = LIGHTGBM
    params: dict = field(default_factory=dict)
    _booster: object | None = None

    def _loaded(self):
        if self._booster is None:
            import lightgbm as lgb

            self._booster = lgb.Booster(model_str=self.booster_text)
        return self._booster

    def score(self, features: dict[str, float]) -> float:
        return float(self._loaded().predict([vector(features)])[0])

    def to_dict(self) -> dict:
        return {"algorithm": self.algorithm, "booster": self.booster_text, "params": self.params}

    @classmethod
    def from_dict(cls, d: dict) -> "BoostedRanker":
        return cls(booster_text=d["booster"], params=d.get("params", {}))


def load(path: Path) -> Scorer:
    d = json.loads(path.read_text(encoding="utf-8"))
    algorithm = d["algorithm"]
    if algorithm == LIGHTGBM:
        return BoostedRanker.from_dict(d)
    if algorithm == LOGISTIC_REGRESSION:
        return LinearRanker.from_dict(d)
    return ComplianceOnlyRanker()


def save(scorer: Scorer, path: Path, metrics: dict, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **scorer.to_dict(),
        "version": version,
        "feature_names": list(FEATURE_NAMES),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# --- fitting -------------------------------------------------------------------------------


def fit_linear(examples: Sequence[Example]) -> LinearRanker:
    from sklearn.linear_model import LogisticRegression

    x = [vector(e.features) for e in examples]
    y = [1 if e.accepted else 0 for e in examples]
    model = LogisticRegression(class_weight="balanced", max_iter=1000)
    model.fit(x, y)
    return LinearRanker(
        coefficients={n: float(w) for n, w in zip(FEATURE_NAMES, model.coef_[0])},
        intercept=float(model.intercept_[0]),
        params={"class_weight": "balanced"},
    )


def fit_boosted(examples: Sequence[Example]) -> BoostedRanker:
    """Deliberately small: few leaves, shallow, strong minimum-leaf size. Review records are
    scarce, and an unconstrained booster would memorise them."""
    import lightgbm as lgb

    params = {
        "objective": "binary",
        "num_leaves": 4,
        "max_depth": 3,
        "min_child_samples": 5,
        "learning_rate": 0.05,
        "n_estimators": 120,
        "subsample": 0.9,
        "subsample_freq": 1,
        "colsample_bytree": 0.8,
        "reg_lambda": 1.0,
        "verbose": -1,
        "random_state": 20260915,
    }
    x = [vector(e.features) for e in examples]
    y = [1 if e.accepted else 0 for e in examples]
    model = lgb.LGBMClassifier(**params)
    model.fit(x, y, feature_name=list(FEATURE_NAMES))
    return BoostedRanker(booster_text=model.booster_.model_to_string(), params=params)
