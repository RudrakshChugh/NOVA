from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import Blueprint, CandidateQuestion, GenerationRun, QuestionBankEntry, User
from ..schemas import BankEntryOut, PublishIn
from ..scoping import COORDINATOR, REVIEWER, course_ids_for, require_candidate, require_entry

router = APIRouter(tags=["bank"])


def _snapshot(db: Session, q: CandidateQuestion) -> dict:
    """Published text plus provenance back to the run, blueprint version, grounding and review (plan §7.1 step 10)."""
    review = max(q.reviews, key=lambda r: r.reviewed_at)
    bp = db.get(Blueprint, q.run.blueprint_id)
    report = q.reports[-1]
    return {
        "stem": review.edited_stem if review.decision == "edit" else q.stem,
        "original_stem": q.stem,
        "answer_key": q.answer_key,
        "options": q.options,
        "constraint": {
            "constraint_id": str(q.constraint.constraint_id),
            "course_outcome": q.constraint.course_outcome,
            "concept": q.constraint.concept,
            "bloom_level": q.constraint.bloom_level,
            "marks": q.constraint.marks,
            "question_type": q.constraint.question_type,
        },
        "blueprint": {"blueprint_id": str(bp.blueprint_id), "version": bp.version, "frozen_at": bp.frozen_at.isoformat()},
        "run": {
            "run_id": str(q.run.run_id),
            "backend_id": q.run.backend_id,
            "prompt_hash": q.run.prompt_hash,
            "temperature": q.run.temperature,
            "seed": q.run.seed,
            "started_at": q.run.started_at.isoformat(),
        },
        "citations": [
            {"chunk_id": str(c.chunk_id), "material": c.chunk.material.filename, "page_ref": c.chunk.page_ref,
             "char_span": c.char_span, "support_score": c.support_score}
            for c in q.citations
        ],
        "compliance": {
            "is_compliant": report.is_compliant,
            "satisfaction_rate": report.satisfaction_rate,
            "repair_attempts": q.repair_attempts,
            "violations": [{"field": v.field, "expected": v.expected, "observed": v.observed} for v in report.violations],
        },
        "review": {
            "record_id": str(review.record_id),
            "reviewer": review.reviewer.email,
            "decision": review.decision,
            "edit_distance": review.edit_distance,
            "reviewed_at": review.reviewed_at.isoformat(),
        },
    }


@router.post("/candidates/publish", response_model=list[BankEntryOut], status_code=201)
def publish(body: PublishIn, db: Session = Depends(get_db),
            user: User = Depends(current_user)):
    entries = []
    for qid in body.question_ids:
        q = require_candidate(db, user, qid, REVIEWER, COORDINATOR)
        if q.state not in ("accepted", "edited"):
            raise HTTPException(409, f"candidate {qid} is {q.state}; only accepted or edited candidates can be published")
        prior = db.scalar(select(func.max(QuestionBankEntry.version)).where(QuestionBankEntry.question_id == qid)) or 0
        entry = QuestionBankEntry(question_id=qid, version=prior + 1, snapshot=_snapshot(db, q))
        q.state = "published"
        db.add(entry)
        entries.append(entry)
    db.commit()
    return entries


@router.get("/bank/entries", response_model=list[BankEntryOut])
def list_entries(course_id: UUID | None = None, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """Published questions from the subjects this person is in, newest first."""
    query = select(QuestionBankEntry).order_by(QuestionBankEntry.published_at.desc())
    scope = course_ids_for(db, user)
    if scope is not None or course_id is not None:
        wanted = [course_id] if course_id is not None else scope
        if scope is not None and course_id is not None:
            wanted = [course_id] if course_id in scope else []
        query = (
            query.join(CandidateQuestion, CandidateQuestion.question_id == QuestionBankEntry.question_id)
            .join(GenerationRun, GenerationRun.run_id == CandidateQuestion.run_id)
            .join(Blueprint, Blueprint.blueprint_id == GenerationRun.blueprint_id)
            .where(Blueprint.course_id.in_(wanted))
        )
    return db.scalars(query).all()


@router.get("/bank/entries/{entry_id}", response_model=BankEntryOut)
def get_entry(entry_id: UUID, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    return require_entry(db, user, entry_id)
