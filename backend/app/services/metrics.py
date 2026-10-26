"""Statistics for every reported number (plan §3.3.6).

Rules the plan fixes and this module enforces:
  * confidence intervals come from 10,000 bootstrap resamples of the underlying data,
    recomputing the statistic on each resample — never from resampling the statistic;
  * a proportion is reported with a Wilson score interval, which stays inside [0, 1] at
    small n where the normal approximation does not;
  * a p-value is never returned without its effect size and interval.
"""

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20260914  # fixed so a reported interval is reproducible


@dataclass(frozen=True)
class Interval:
    point: float
    low: float
    high: float

    def as_dict(self) -> dict[str, float | None]:
        """An undefined statistic — no data, or none on which it is defined — serialises as null
        rather than NaN, which is not valid JSON and would otherwise read as a real zero."""

        def value(x: float) -> float | None:
            return None if math.isnan(x) or math.isinf(x) else x

        return {"point": value(self.point), "low": value(self.low), "high": value(self.high)}

    def __str__(self) -> str:
        return f"{self.point:.3f} [{self.low:.3f}, {self.high:.3f}]"


def bootstrap_ci(
    data: Sequence,
    statistic: Callable[[Sequence], float | None],
    resamples: int = BOOTSTRAP_RESAMPLES,
    alpha: float = 0.05,
    seed: int = BOOTSTRAP_SEED,
) -> Interval:
    """Percentile bootstrap. `statistic` is recomputed on each resample of `data`; resamples on
    which it is undefined (e.g. F1 with no positives drawn) are skipped."""
    point = statistic(data)
    if point is None or not data:
        return Interval(float("nan"), float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(data)
    values = []
    for _ in range(resamples):
        sample = [data[rng.randrange(n)] for _ in range(n)]
        value = statistic(sample)
        if value is not None:
            values.append(value)
    if not values:
        return Interval(point, float("nan"), float("nan"))
    values.sort()
    lo = values[max(0, int((alpha / 2) * len(values)) - 1)]
    hi = values[min(len(values) - 1, int((1 - alpha / 2) * len(values)))]
    return Interval(point, lo, hi)


def wilson_interval(successes: int, n: int, z: float = 1.959963985) -> Interval:
    """Wilson score interval for a proportion (95% by default)."""
    if n == 0:
        return Interval(float("nan"), float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return Interval(p, max(0.0, centre - half), min(1.0, centre + half))


# --- classification ------------------------------------------------------------------------


def precision(pairs: Sequence[tuple[bool, bool]]) -> float | None:
    """pairs are (y_true, y_pred). None when the prediction set is empty (undefined)."""
    predicted = [t for t, p in pairs if p]
    return (sum(1 for t in predicted if t) / len(predicted)) if predicted else None


def recall(pairs: Sequence[tuple[bool, bool]]) -> float | None:
    actual = [p for t, p in pairs if t]
    return (sum(1 for p in actual if p) / len(actual)) if actual else None


def f1(pairs: Sequence[tuple[bool, bool]]) -> float | None:
    p, r = precision(pairs), recall(pairs)
    if p is None or r is None or p + r == 0:
        return None
    return 2 * p * r / (p + r)


def confusion(pairs: Sequence[tuple[bool, bool]]) -> dict[str, int]:
    tp = sum(1 for t, p in pairs if t and p)
    fp = sum(1 for t, p in pairs if not t and p)
    fn = sum(1 for t, p in pairs if t and not p)
    tn = sum(1 for t, p in pairs if not t and not p)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


# --- paired comparison ---------------------------------------------------------------------


@dataclass(frozen=True)
class McNemarResult:
    """Exact McNemar test on paired binary outcomes, with the effect size the plan requires
    alongside it (plan §3.3.6: no p-value without its effect size and interval)."""

    b: int  # system A succeeded, B failed
    c: int  # A failed, B succeeded
    p_value: float
    difference: Interval  # proportion(A) - proportion(B), bootstrap CI

    def as_dict(self) -> dict:
        return {"b": self.b, "c": self.c, "p_value": self.p_value, "difference": self.difference.as_dict()}


def mcnemar(paired: Sequence[tuple[bool, bool]], seed: int = BOOTSTRAP_SEED) -> McNemarResult:
    """`paired` is one (outcome_A, outcome_B) per item, both systems having seen the same item."""
    b = sum(1 for a, bb in paired if a and not bb)
    c = sum(1 for a, bb in paired if not a and bb)
    p_value = _exact_binomial_two_sided(b, b + c)
    diff = bootstrap_ci(
        paired,
        lambda s: (sum(1 for a, _ in s if a) - sum(1 for _, bb in s if bb)) / len(s) if s else None,
        seed=seed,
    )
    return McNemarResult(b=b, c=c, p_value=p_value, difference=diff)


def _exact_binomial_two_sided(k: int, n: int) -> float:
    """Two-sided exact binomial test against p = 0.5."""
    if n == 0:
        return 1.0
    pmf = [math.comb(n, i) * 0.5**n for i in range(n + 1)]
    threshold = pmf[k] * (1 + 1e-9)
    return min(1.0, sum(p for p in pmf if p <= threshold))


def cohens_kappa(paired: Sequence[tuple[str, str]]) -> float | None:
    """Agreement between two raters on the same items, corrected for chance."""
    if not paired:
        return None
    n = len(paired)
    observed = sum(1 for a, b in paired if a == b) / n
    labels = {label for pair in paired for label in pair}
    expected = sum(
        (sum(1 for a, _ in paired if a == label) / n) * (sum(1 for _, b in paired if b == label) / n)
        for label in labels
    )
    if expected >= 1.0:
        return None  # no chance-corrected agreement is defined when agreement is forced
    return (observed - expected) / (1 - expected)


# --- ranking -------------------------------------------------------------------------------


def ndcg_at_k(relevances: Sequence[float], k: int) -> float | None:
    """NDCG@k over a ranked list, `relevances` in the predicted order (plan §3.3.5)."""
    if not relevances or k <= 0:
        return None
    gains = [(2**r - 1) / math.log2(i + 2) for i, r in enumerate(relevances[:k])]
    ideal_order = sorted(relevances, reverse=True)[:k]
    ideal = [(2**r - 1) / math.log2(i + 2) for i, r in enumerate(ideal_order)]
    ideal_sum = sum(ideal)
    return (sum(gains) / ideal_sum) if ideal_sum > 0 else None
