from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import require_role
from ..db import get_db
from ..models import CandidateQuestion, ReviewRecord, User
from ..schemas import CandidateOut, ReviewIn, ReviewSummaryOut
from ..services.review import normalised_edit_distance
from .runs import candidate_out, get_run

router = APIRouter(tags=["review"])

_STATE_FOR = {"accept": "accepted", "edit": "edited", "reject": "rejected"}


@router.post("/candidates/{question_id}/review", response_model=CandidateOut)
def review(question_id: UUID, body: ReviewIn, user: User = Depends(require_role("reviewer")), db: Session = Depends(get_db)):
    q = db.get(CandidateQuestion, question_id)
    if q is None:
        raise HTTPException(404, "candidate not found")
    if q.state in ("published", "superseded"):
        raise HTTPException(409, f"candidate is {q.state}")
    if q.run.status != "completed":
        raise HTTPException(409, "generation run has not completed")

    record = ReviewRecord(question_id=q.question_id, reviewer_id=user.user_id, decision=body.decision, reason=body.reason)
    if body.decision == "edit":
        if not body.edited_stem or body.edited_stem.strip() == q.stem:
            raise HTTPException(422, "edit requires an edited_stem different from the original")
        record.edited_stem = body.edited_stem.strip()
        record.edit_distance = normalised_edit_distance(q.stem, record.edited_stem)
    elif body.decision == "reject" and not body.reason:
        raise HTTPException(422, "reject requires a reason")
    q.state = _STATE_FOR[body.decision]
    db.add(record)
    db.commit()
    db.refresh(q)
    return candidate_out(q)


@router.get("/runs/{run_id}/reviews", response_model=ReviewSummaryOut)
def review_summary(run_id: UUID, db: Session = Depends(get_db)):
    """Counts the latest review decision per candidate (publishing does not change the decision)."""
    run = get_run(db, run_id)
    counts = {"accept": 0, "edit": 0, "reject": 0}
    distances: list[float] = []
    for q in run.candidates:
        latest = max(q.reviews, key=lambda r: r.reviewed_at, default=None)
        if latest is None:
            continue
        counts[latest.decision] += 1
        if latest.edit_distance is not None:
            distances.append(latest.edit_distance)
    return ReviewSummaryOut(
        run_id=run.run_id, total=len(run.candidates),
        accepted=counts["accept"], edited=counts["edit"], rejected=counts["reject"],
        pending=len(run.candidates) - sum(counts.values()),
        mean_edit_distance=sum(distances) / len(distances) if distances else None,
    )
