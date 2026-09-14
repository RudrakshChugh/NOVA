from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_role
from ..db import get_db
from ..models import Blueprint, CandidateQuestion, ComplianceReport, GenerationRun
from ..schemas import CandidateOut, CitationOut, ComplianceOut, ConstraintOut, EvidenceOut, ReviewOut, RunOut, UserOut, ViolationOut
from ..services import orchestrator

router = APIRouter(tags=["generation"])


def run_out(run: GenerationRun) -> RunOut:
    return RunOut(
        run_id=run.run_id, blueprint_id=run.blueprint_id, backend_id=run.backend_id, prompt_hash=run.prompt_hash,
        temperature=run.temperature, seed=run.seed, started_at=run.started_at, status=run.status, error=run.error,
        candidate_count=len(run.candidates),
    )


def compliance_out(r: ComplianceReport) -> ComplianceOut:
    return ComplianceOut(
        report_id=r.report_id, is_compliant=r.is_compliant, satisfaction_rate=r.satisfaction_rate,
        violations=[ViolationOut(field=v.field, expected=v.expected, observed=v.observed) for v in r.violations],
    )


def citations_out(q: CandidateQuestion) -> list[CitationOut]:
    return [
        CitationOut(citation_id=c.citation_id, chunk_id=c.chunk_id, page_ref=c.chunk.page_ref, text=c.chunk.text,
                    char_span=c.char_span, support_score=c.support_score)
        for c in q.citations
    ]


def candidate_out(q: CandidateQuestion) -> CandidateOut:
    latest_review = max(q.reviews, key=lambda r: r.reviewed_at, default=None)
    return CandidateOut(
        question_id=q.question_id, run_id=q.run_id,
        constraint=ConstraintOut.model_validate(q.constraint, from_attributes=True),
        stem=q.stem, answer_key=q.answer_key, options=q.options, state=q.state, repair_attempts=q.repair_attempts,
        citations=citations_out(q),
        compliance=compliance_out(q.reports[-1]) if q.reports else None,
        review=ReviewOut(
            record_id=latest_review.record_id, reviewer=UserOut.model_validate(latest_review.reviewer, from_attributes=True),
            decision=latest_review.decision, edited_stem=latest_review.edited_stem, edit_distance=latest_review.edit_distance,
            reason=latest_review.reason, reviewed_at=latest_review.reviewed_at,
        ) if latest_review else None,
    )


def get_run(db: Session, run_id: UUID) -> GenerationRun:
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@router.post("/blueprints/{blueprint_id}/generate", response_model=RunOut, status_code=202, dependencies=[Depends(require_role("reviewer"))])
def generate(blueprint_id: UUID, background: BackgroundTasks, db: Session = Depends(get_db)):
    bp = db.get(Blueprint, blueprint_id)
    if bp is None:
        raise HTTPException(404, "blueprint not found")
    if bp.frozen_at is None:
        raise HTTPException(409, "blueprint must be frozen before generation")
    run = orchestrator.start_run(db, bp)
    background.add_task(orchestrator.execute_run, run.run_id)
    return run_out(run)


@router.get("/blueprints/{blueprint_id}/runs", response_model=list[RunOut])
def list_runs(blueprint_id: UUID, db: Session = Depends(get_db)):
    runs = db.scalars(select(GenerationRun).where(GenerationRun.blueprint_id == blueprint_id).order_by(GenerationRun.started_at.desc())).all()
    return [run_out(r) for r in runs]


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run_status(run_id: UUID, db: Session = Depends(get_db)):
    return run_out(get_run(db, run_id))


@router.get("/runs/{run_id}/candidates", response_model=list[CandidateOut])
def list_candidates(run_id: UUID, db: Session = Depends(get_db)):
    """Ordered by blueprint position, then compliance (the ranker is a stub in the prototype, plan §7.2)."""
    run = get_run(db, run_id)
    ordered = sorted(run.candidates, key=lambda q: (q.constraint.position, q.state != "compliant"))
    return [candidate_out(q) for q in ordered]


@router.get("/candidates/{question_id}/evidence", response_model=EvidenceOut)
def evidence(question_id: UUID, db: Session = Depends(get_db)):
    q = db.get(CandidateQuestion, question_id)
    if q is None:
        raise HTTPException(404, "candidate not found")
    return EvidenceOut(question_id=q.question_id, citations=citations_out(q), reports=[compliance_out(r) for r in q.reports])
