"""Reviewer-adapted ranker (plan §3.1): orders candidates by predicted acceptance, learned from
the accept / edit / reject records reviewers have already produced.

This is a separately fitted preference model, not few-shot prompting: no past example is pasted
into the generation prompt, and the ranker runs after both audits, on their outputs.
"""

from pathlib import Path

from ...config import settings
from .models import Scorer, load


def active_ranker_path() -> Path:
    return settings.ranker_model_path


def load_active() -> Scorer | None:
    """The fitted ranker, or None when none has been fitted yet."""
    path = active_ranker_path()
    return load(path) if path.exists() else None


def order(candidates: list) -> list:
    """Candidates best-first by predicted acceptance, falling back to blueprint order when no
    ranker is fitted. Candidates that failed an audit sort after those that passed either way,
    so a reviewer is never shown a known-bad question first."""
    from .features import extract

    scorer = load_active()
    if scorer is None:
        return sorted(candidates, key=lambda q: (q.constraint.position, q.state != "novel"))
    scored = [(q, scorer.score(extract(q))) for q in candidates]
    scored.sort(key=lambda row: (row[0].state not in ("novel", "accepted", "edited", "published"), -row[1]))
    return [q for q, _ in scored]
