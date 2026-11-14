from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import CandidateQuestion, ComplianceReport, GenerationRun, NoveltyReport, User
from ..scoping import COORDINATOR, REVIEWER, require_blueprint, require_candidate, require_run
from ..schemas import (
    CandidateOut, CheckOut, CitationOut, ComplianceOut, ConstraintOut, EvidenceOut, GenerateIn, NoveltyOut,
    NoveltySignalOut, PYQOut, ReviewOut, RunOut, UserOut, ViolationOut,
)
from ..services import compliance, orchestrator, ranker

router = APIRouter(tags=["generation"])


def run_out(run: GenerationRun) -> RunOut:
    return RunOut(
        run_id=run.run_id, blueprint_id=run.blueprint_id, backend_id=run.backend_id, prompt_hash=run.prompt_hash,
        temperature=run.temperature, seed=run.seed, started_at=run.started_at, status=run.status, error=run.error,
        suggestion=run.suggestion,
        candidate_count=len(run.candidates), constraint_count=len(run.blueprint.constraints),
        arm_label=run.arm_label,
    )


def compliance_out(r: ComplianceReport) -> ComplianceOut:
    failed = {v.field: v for v in r.violations}
    return ComplianceOut(
        report_id=r.report_id, is_compliant=r.is_compliant, satisfaction_rate=r.satisfaction_rate,
        violations=[ViolationOut(field=v.field, expected=v.expected, observed=v.observed) for v in r.violations],
        checks=[
            CheckOut(field=f, passed=f not in failed, expected=failed[f].expected if f in failed else None,
                     observed=failed[f].observed if f in failed else None)
            for f in compliance.checked_fields(r.question.constraint)
        ],
    )


def novelty_out(r: NoveltyReport) -> NoveltyOut:
    return NoveltyOut(
        report_id=r.report_id, duplicate_probability=r.duplicate_probability, threshold=r.threshold,
        is_duplicate=r.is_duplicate, model_version=r.model_version,
        signals=[NoveltySignalOut(name=s.name, value=s.value, weight=s.weight) for s in r.signals],
        nearest_pyq=PYQOut.model_validate(r.nearest_pyq, from_attributes=True) if r.nearest_pyq else None,
        nearest_paper=f"{r.nearest_pyq.paper.label} ({r.nearest_pyq.paper.year})" if r.nearest_pyq else None,
    )


def citations_out(q: CandidateQuestion) -> list[CitationOut]:
    return [
        CitationOut(citation_id=c.citation_id, chunk_id=c.chunk_id, page_ref=c.chunk.page_ref, text=c.chunk.text,
                    char_span=c.char_span, support_score=c.support_score)
        for c in q.citations
    ]


def candidate_out(q: CandidateQuestion, reveal_arm: bool = True) -> CandidateOut:
    """`reveal_arm=False` also withholds the run id: during a blinded evaluation the reviewer must
    not be able to follow it back to the arm that produced the question (plan §3.3.4)."""
    latest_review = max(q.reviews, key=lambda r: r.reviewed_at, default=None)
    return CandidateOut(
        question_id=q.question_id, run_id=q.run_id if reveal_arm else None,
        constraint=ConstraintOut.model_validate(q.constraint, from_attributes=True),
        stem=q.stem, answer_key=q.answer_key, options=q.options, state=q.state, repair_attempts=q.repair_attempts,
        citations=citations_out(q),
        compliance=compliance_out(q.reports[-1]) if q.reports else None,
        novelty=novelty_out(q.novelty_reports[-1]) if q.novelty_reports else None,
        review=ReviewOut(
            record_id=latest_review.record_id, reviewer=UserOut.model_validate(latest_review.reviewer, from_attributes=True),
            decision=latest_review.decision, edited_stem=latest_review.edited_stem, edit_distance=latest_review.edit_distance,
            reason=latest_review.reason, reviewed_at=latest_review.reviewed_at,
        ) if latest_review else None,
    )


def get_run(db: Session, run_id: UUID, user: User, *roles: str) -> GenerationRun:
    """Scoped lookup. Shared with the review router, which addresses runs the same way."""
    return require_run(db, user, run_id, *roles)


@router.post("/blueprints/{blueprint_id}/generate", response_model=RunOut, status_code=202)
def generate(blueprint_id: UUID, background: BackgroundTasks, body: GenerateIn | None = None,
             db: Session = Depends(get_db), user: User = Depends(current_user)):
    """`suggestion` is optional and free text: what this reviewer is after, in their own words.

    It cannot loosen the blueprint — compliance still judges the draft against the constraint
    row — and it is recorded on the run, both so the run is reproducible and so hinted runs can
    be kept out of the evaluation, where a hint is a confound (plan.md §14.4)."""
    bp = require_blueprint(db, user, blueprint_id, REVIEWER, COORDINATOR)
    if bp.frozen_at is None:
        raise HTTPException(409, "blueprint must be frozen before generation")
    run = orchestrator.start_run(db, bp, suggestion=body.suggestion if body else None)
    background.add_task(orchestrator.execute_run, run.run_id)
    return run_out(run)


@router.get("/blueprints/{blueprint_id}/runs", response_model=list[RunOut])
def list_runs(blueprint_id: UUID, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    require_blueprint(db, user, blueprint_id)
    runs = db.scalars(select(GenerationRun).where(GenerationRun.blueprint_id == blueprint_id).order_by(GenerationRun.started_at.desc())).all()
    return [run_out(r) for r in runs]


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run_status(run_id: UUID, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    return run_out(get_run(db, run_id, user))


@router.get("/runs/{run_id}/candidates", response_model=list[CandidateOut])
def list_candidates(run_id: UUID, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    """Ranked by the reviewer-adapted ranker when one is fitted; otherwise blueprint order with
    the candidates that survived both audits first."""
    run = get_run(db, run_id, user)
    return [candidate_out(q) for q in ranker.order(list(run.candidates), run.blueprint.course_id)]


@router.get("/candidates/{question_id}/evidence", response_model=EvidenceOut)
def evidence(question_id: UUID, db: Session = Depends(get_db),
             user: User = Depends(current_user)):
    q = require_candidate(db, user, question_id)
    return EvidenceOut(question_id=q.question_id, citations=citations_out(q), reports=[compliance_out(r) for r in q.reports])
