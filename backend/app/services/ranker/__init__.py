"""Reviewer-adapted ranker (plan §3.1): orders candidates by predicted acceptance, learned from
the accept / edit / reject records reviewers have already produced.

This is a separately fitted preference model, not few-shot prompting: no past example is pasted
into the generation prompt, and the ranker runs after both audits, on their outputs.
"""

from pathlib import Path
from uuid import UUID

from ...config import settings
from .models import FeatureSetMismatch, Scorer, load


def active_ranker_path(course_id: UUID) -> Path:
    """One fitted ranker per subject: it is fitted on that subject’s reviewers and their
    decisions, which is exactly what makes it reviewer-adapted."""
    base = settings.ranker_model_path
    return base.with_name(f"{base.stem}_{course_id}{base.suffix}")


def load_active(course_id: UUID) -> Scorer | None:
    """The fitted ranker, or None when none has been fitted yet — or when the one on disk was
    fitted on a feature list this build no longer has. Ordering then falls back to blueprint
    position, and the ranker model card says why, rather than a stale model quietly scoring."""
    path = active_ranker_path(course_id)
    if not path.exists():
        return None
    try:
        return load(path)
    except FeatureSetMismatch:
        return None


def order(candidates: list, course_id: UUID) -> list:
    """Candidates best-first by predicted acceptance, falling back to blueprint order when no
    ranker is fitted. Candidates that failed an audit sort after those that passed either way,
    so a reviewer is never shown a known-bad question first."""
    from .features import extract

    scorer = load_active(course_id)
    if scorer is None:
        return sorted(candidates, key=lambda q: (q.constraint.position, q.state != "novel"))
    scored = [(q, scorer.score(extract(q))) for q in candidates]
    scored.sort(key=lambda row: (row[0].state not in ("novel", "accepted", "edited", "published"), -row[1]))
    return [q for q, _ in scored]
