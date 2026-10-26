"""Fitting and honest comparison of the ranker (plan §3.1, §3.3.5, §11 step 3).

Protocol:
  * Examples are grouped into review sittings — one reviewer working through one run.
  * Leave-one-session-out cross-validation holds a whole sitting out, so the model is never fitted
    on decisions from the sitting it is scored on.
  * Models are compared on NDCG@k over the held-out sitting, because the ranker's claim is about
    order, not about classification accuracy.
  * The last sitting chronologically is additionally reported on its own, so temporal leakage is
    impossible for the headline number.
  * LightGBM is activated only if it beats both floors. Otherwise the simpler model ships and the
    comparison is recorded either way.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import CandidateQuestion, GenerationRun, ModelVersion
from ..metrics import bootstrap_ci, ndcg_at_k
from . import active_ranker_path
from .features import decision_of, extract, session_of
from .models import (
    COMPLIANCE_ONLY,
    LIGHTGBM,
    LOGISTIC_REGRESSION,
    ComplianceOnlyRanker,
    Example,
    fit_boosted,
    fit_linear,
    save,
)

MIN_SESSIONS = 3
MIN_EXAMPLES = 20


def collect(db: Session, course_id: UUID) -> list[Example]:
    """Every reviewed candidate of a course, as a labelled example."""
    candidates = db.scalars(
        select(CandidateQuestion)
        .join(GenerationRun, GenerationRun.run_id == CandidateQuestion.run_id)
        .join(GenerationRun.blueprint)
        .where(GenerationRun.blueprint.has(course_id=course_id))
    ).all()
    out = []
    for candidate in candidates:
        decision = decision_of(candidate)
        session = session_of(candidate)
        if decision is None or session is None:
            continue
        out.append(
            Example(
                features=extract(candidate), accepted=decision.accepted_without_edit,
                relevance=decision.relevance, session=session,
            )
        )
    return out


def _sessions(examples: list[Example]) -> dict[tuple, list[Example]]:
    grouped: dict[tuple, list[Example]] = {}
    for e in examples:
        grouped.setdefault(e.session, []).append(e)
    return grouped


def _ndcg_for(scorer, held_out: list[Example]) -> float | None:
    ordered = sorted(held_out, key=lambda e: scorer.score(e.features), reverse=True)
    return ndcg_at_k([e.relevance for e in ordered], k=len(ordered))


def _fit_all(train: list[Example]) -> dict:
    """The three models, when there is enough signal to fit the learned ones."""
    labels = {e.accepted for e in train}
    built = {COMPLIANCE_ONLY: ComplianceOnlyRanker()}
    if len(labels) == 2:  # a single-class fold can only support the rule-based floor
        built[LOGISTIC_REGRESSION] = fit_linear(train)
        built[LIGHTGBM] = fit_boosted(train)
    return built


def cross_validate(examples: list[Example]) -> dict:
    """Leave-one-session-out NDCG@k per model, with a bootstrap interval over sittings."""
    grouped = _sessions(examples)
    per_model: dict[str, list[float]] = {}
    for key, held_out in grouped.items():
        train = [e for e in examples if e.session != key]
        if not train or len(held_out) < 2:
            continue
        for name, scorer in _fit_all(train).items():
            value = _ndcg_for(scorer, held_out)
            if value is not None:
                per_model.setdefault(name, []).append(value)

    return {
        name: {
            "sessions": len(values),
            "ndcg": bootstrap_ci(values, lambda s: sum(s) / len(s) if s else None).as_dict(),
        }
        for name, values in per_model.items()
    }


def choose(comparison: dict) -> tuple[str, bool]:
    """Which model ships. LightGBM only if its cross-validated NDCG beats *both* floors;
    otherwise the best-scoring simpler model. Returns (name, lightgbm_beat_both_floors)."""

    def point(name: str) -> float:
        entry = comparison.get(name)
        return entry["ndcg"]["point"] if entry else float("-inf")

    floors = [n for n in (COMPLIANCE_ONLY, LOGISTIC_REGRESSION) if n in comparison]
    beats_both = LIGHTGBM in comparison and bool(floors) and all(point(LIGHTGBM) > point(f) for f in floors)
    if beats_both:
        return LIGHTGBM, True
    simpler = [n for n in comparison if n != LIGHTGBM]
    if not simpler:
        return COMPLIANCE_ONLY, False
    return max(simpler, key=point), False


def _last_session(examples: list[Example], db: Session) -> tuple | None:
    """The chronologically last sitting, identified by its most recent review."""
    grouped = _sessions(examples)
    if len(grouped) < 2:
        return None
    from ...models import ReviewRecord

    latest: dict[tuple, object] = {}
    for key in grouped:
        reviewer_id, run_id = key
        when = db.scalar(
            select(func.max(ReviewRecord.reviewed_at))
            .join(CandidateQuestion, CandidateQuestion.question_id == ReviewRecord.question_id)
            .where(ReviewRecord.reviewer_id == reviewer_id, CandidateQuestion.run_id == run_id)
        )
        latest[key] = when
    return max(latest, key=lambda k: (latest[k] is not None, latest[k]))


def train(db: Session, course_id: UUID) -> dict:
    examples = collect(db, course_id)
    grouped = _sessions(examples)
    if len(examples) < MIN_EXAMPLES or len(grouped) < MIN_SESSIONS:
        return {
            "trained": False,
            "reason": (
                f"need at least {MIN_EXAMPLES} reviewed candidates across {MIN_SESSIONS} review "
                f"sittings; have {len(examples)} across {len(grouped)}"
            ),
            "examples": len(examples),
            "sessions": len(grouped),
        }

    comparison = cross_validate(examples)
    chosen_name, beats_both = choose(comparison)

    held_out_key = _last_session(examples, db)
    held_out_metrics = {}
    if held_out_key is not None:
        held_out = grouped[held_out_key]
        train_set = [e for e in examples if e.session != held_out_key]
        held_out_metrics = {
            name: _ndcg_for(scorer, held_out) for name, scorer in _fit_all(train_set).items()
        }

    final = _fit_all(examples).get(chosen_name, ComplianceOnlyRanker())
    version = (db.scalar(select(func.max(ModelVersion.version)).where(ModelVersion.kind == "ranker")) or 0) + 1
    metrics = {
        "cross_validation": comparison,
        "last_session_ndcg": held_out_metrics,
        "n_examples": len(examples),
        "n_sessions": len(grouped),
        "selection": {
            "chosen": chosen_name,
            "lightgbm_beat_both_floors": beats_both,
            "rule": "LightGBM ships only if its leave-one-session-out NDCG beats both floors",
        },
    }
    save(final, active_ranker_path(), metrics, version)

    db.query(ModelVersion).filter_by(kind="ranker", is_active=True).update({"is_active": False})
    db.add(
        ModelVersion(
            kind="ranker", algorithm=chosen_name, version=version,
            params=getattr(final, "params", {}), metrics=metrics,
            artifact_path=str(active_ranker_path()), is_active=True,
        )
    )
    db.commit()
    return {"trained": True, **model_card(db)}


def model_card(db: Session) -> dict:
    row = db.scalar(select(ModelVersion).where(ModelVersion.kind == "ranker", ModelVersion.is_active))
    if row is None:
        return {
            "is_fitted": False,
            "algorithm": None,
            "note": "No ranker has been fitted. Candidates are ordered by blueprint position, then by whether they survived both audits.",
        }
    return {
        "is_fitted": True,
        "algorithm": row.algorithm,
        "version": row.version,
        "params": row.params,
        "metrics": row.metrics,
        "trained_at": row.trained_at.isoformat(),
    }
