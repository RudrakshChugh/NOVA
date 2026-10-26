"""Novelty model lifecycle: build the labelled pair set, fit the combiner, report held-out
performance. The test fold is behind its own endpoint because plan §5 reserves it for a single
reported evaluation."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_role
from ..db import get_db
from ..models import Course, DuplicatePair
from ..schemas import DuplicatePairOut, PairBuildIn
from ..services.novelty import dataset, training
from ..services.ranker import training as ranker_training

router = APIRouter(tags=["novelty"])


def _require_course(db: Session, course_id: UUID) -> Course:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(404, "course not found")
    return course


@router.post("/courses/{course_id}/novelty/pairs", dependencies=[Depends(require_role("coordinator"))])
def build_pairs(course_id: UUID, body: PairBuildIn, db: Session = Depends(get_db)):
    """Construct labelled pairs from the course's past-year corpus. Pairs already present keep
    their fold; new ones are assigned by the committed rule."""
    _require_course(db, course_id)
    return dataset.build_pair_set(
        db, course_id,
        positives_per_question=body.positives_per_question,
        hard_negatives_per_question=body.hard_negatives_per_question,
        easy_negatives_per_question=body.easy_negatives_per_question,
    )


@router.get("/novelty/pairs")
def pair_stats(db: Session = Depends(get_db)):
    return dataset.stats(db)


@router.get("/novelty/pairs/list", response_model=list[DuplicatePairOut])
def list_pairs(split: str | None = None, stratum: str | None = None, limit: int = 50,
               db: Session = Depends(get_db)):
    query = select(DuplicatePair)
    if split:
        query = query.where(DuplicatePair.split == split)
    if stratum:
        query = query.where(DuplicatePair.stratum == stratum)
    return db.scalars(query.order_by(DuplicatePair.created_at).limit(limit)).all()


@router.post("/novelty/pairs/export", dependencies=[Depends(require_role("coordinator"))])
def export_split(db: Session = Depends(get_db)):
    """Write the split manifest that is committed to the repository alongside the seed."""
    return dataset.export_split_manifest(db)


@router.post("/courses/{course_id}/novelty/train", dependencies=[Depends(require_role("coordinator"))])
def train_combiner(course_id: UUID, db: Session = Depends(get_db)):
    _require_course(db, course_id)
    return training.train(db, course_id)


@router.post("/courses/{course_id}/novelty/evaluate-test", dependencies=[Depends(require_role("coordinator"))])
def evaluate_on_test(course_id: UUID, db: Session = Depends(get_db)):
    """Held-out evaluation. Every access is recorded on the model so the count can be reported."""
    _require_course(db, course_id)
    return training.evaluate_test(db, course_id)


@router.get("/novelty/model")
def model_card(db: Session = Depends(get_db)):
    return training.model_card(db)


# --- Reviewer-adapted ranker (plan §3.1) ---


@router.post("/courses/{course_id}/ranker/train", dependencies=[Depends(require_role("coordinator"))])
def train_ranker(course_id: UUID, db: Session = Depends(get_db)):
    """Fit and compare against both floors. The simpler model ships if LightGBM does not win."""
    _require_course(db, course_id)
    return ranker_training.train(db, course_id)


@router.get("/ranker/model")
def ranker_card(db: Session = Depends(get_db)):
    return ranker_training.model_card(db)
