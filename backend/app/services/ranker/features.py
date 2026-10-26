"""Features the ranker may see (plan §3.1).

Leakage rule: every feature here is computable *before* a reviewer looks at the question. Nothing
derived from the decision on that question — its acceptance label, its edit distance, its reject
reason — may appear. The list is explicit rather than generated so a reader can check that.
"""

from dataclasses import dataclass

BLOOM_ORDER = ("remember", "understand", "apply", "analyze", "evaluate", "create")
DIFFICULTY_ORDER = ("easy", "medium", "hard")
QUESTION_TYPES = ("mcq", "short_answer", "long_answer", "numerical")

FEATURE_NAMES = (
    # blueprint compliance
    "satisfaction_rate",
    "is_compliant",
    "repair_attempts",
    "violation_count",
    # novelty
    "duplicate_probability",
    "signal_semantic",
    "signal_lexical",
    "signal_structural",
    "signal_numerical",
    "novelty_checked",
    # grounding
    "citation_count",
    "max_support",
    "mean_support",
    # the constraint it was written for
    "bloom_rank",
    "marks",
    "difficulty_rank",
    *(f"type_{t}" for t in QUESTION_TYPES),
    # the question itself
    "stem_words",
    "answer_words",
    "has_options",
)


@dataclass(frozen=True)
class Decision:
    """A reviewer's verdict, used as the label — never as a feature."""

    accepted_without_edit: bool
    relevance: int  # accept 2, edit 1, reject 0 — the graded relevance NDCG uses


def decision_of(candidate) -> Decision | None:
    """The latest review of a candidate, or None if nobody has reviewed it."""
    if not candidate.reviews:
        return None
    latest = max(candidate.reviews, key=lambda r: r.reviewed_at)
    grade = {"accept": 2, "edit": 1, "reject": 0}[latest.decision]
    return Decision(accepted_without_edit=latest.decision == "accept", relevance=grade)


def session_of(candidate) -> tuple | None:
    """Which review sitting a candidate belongs to: one reviewer working through one run.
    Leave-one-session-out cross-validation holds these out whole, so the ranker is never fitted
    on decisions from the same sitting it is asked to predict."""
    if not candidate.reviews:
        return None
    latest = max(candidate.reviews, key=lambda r: r.reviewed_at)
    return (str(latest.reviewer_id), str(candidate.run_id))


def extract(candidate) -> dict[str, float]:
    """The feature vector for one candidate. Missing evidence is encoded explicitly rather than
    imputed, so a question the novelty engine never saw is distinguishable from one it cleared."""
    report = candidate.reports[-1] if candidate.reports else None
    novelty = candidate.novelty_reports[-1] if candidate.novelty_reports else None
    supports = [c.support_score for c in candidate.citations]
    constraint = candidate.constraint
    signals = {s.name: s.value for s in novelty.signals} if novelty else {}

    features: dict[str, float] = {
        "satisfaction_rate": report.satisfaction_rate if report else 0.0,
        "is_compliant": float(bool(report and report.is_compliant)),
        "repair_attempts": float(candidate.repair_attempts),
        "violation_count": float(len(report.violations)) if report else 0.0,
        "duplicate_probability": novelty.duplicate_probability if novelty else 0.0,
        "signal_semantic": signals.get("semantic", 0.0),
        "signal_lexical": signals.get("lexical", 0.0),
        "signal_structural": signals.get("structural", 0.0),
        "signal_numerical": signals.get("numerical", 0.0),
        "novelty_checked": float(novelty is not None),
        "citation_count": float(len(supports)),
        "max_support": max(supports) if supports else 0.0,
        "mean_support": (sum(supports) / len(supports)) if supports else 0.0,
        "bloom_rank": float(BLOOM_ORDER.index(constraint.bloom_level)) if constraint.bloom_level in BLOOM_ORDER else 0.0,
        "marks": float(constraint.marks),
        "difficulty_rank": float(DIFFICULTY_ORDER.index(constraint.target_difficulty))
        if constraint.target_difficulty in DIFFICULTY_ORDER
        else 0.0,
        "stem_words": float(len(candidate.stem.split())),
        "answer_words": float(len(candidate.answer_key.split())),
        "has_options": float(bool(candidate.options)),
    }
    for t in QUESTION_TYPES:
        features[f"type_{t}"] = float(constraint.question_type == t)
    return features


def vector(features: dict[str, float]) -> list[float]:
    return [features[name] for name in FEATURE_NAMES]
