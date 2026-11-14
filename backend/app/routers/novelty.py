"""Novelty and ranker model lifecycle, per subject.

Every route here is addressed under its course. A combiner is fitted on one subject's past-year
corpus and a ranker on one subject's reviewers, so neither says anything about another subject —
and a platform-wide endpoint would have quietly served whichever subject was trained last
(plan.md §14.1).

The test fold is behind its own endpoint because plan §5 reserves it for a single reported
evaluation.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import DuplicatePair, User
from ..schemas import DuplicatePairOut, PairBuildIn
from ..scoping import COORDINATOR, require_course
from ..services.novelty import dataset, training
from ..services.ranker import training as ranker_training

router = APIRouter(tags=["novelty"])


@router.post("/courses/{course_id}/novelty/pairs")
def build_pairs(course_id: UUID, body: PairBuildIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """Construct labelled pairs from this subject's past-year corpus. Pairs already present keep
    their fold; new ones are assigned by the committed rule."""
    require_course(db, user, course_id, COORDINATOR)
    return dataset.build_pair_set(
        db, course_id,
        positives_per_question=body.positives_per_question,
        hard_negatives_per_question=body.hard_negatives_per_question,
        easy_negatives_per_question=body.easy_negatives_per_question,
    )


@router.get("/courses/{course_id}/novelty/pairs")
def pair_stats(course_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_course(db, user, course_id)
    return dataset.stats(db, course_id)


@router.get("/courses/{course_id}/novelty/pairs/list", response_model=list[DuplicatePairOut])
def list_pairs(course_id: UUID, split: str | None = None, stratum: str | None = None,
               limit: int = 50, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_course(db, user, course_id)
    query = select(DuplicatePair).where(DuplicatePair.course_id == course_id)
    if split:
        query = query.where(DuplicatePair.split == split)
    if stratum:
        query = query.where(DuplicatePair.stratum == stratum)
    return db.scalars(query.order_by(DuplicatePair.created_at).limit(limit)).all()


@router.post("/courses/{course_id}/novelty/pairs/export")
def export_split(course_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Write the split manifest that is committed to the repository alongside the seed."""
    require_course(db, user, course_id, COORDINATOR)
    return dataset.export_split_manifest(db, course_id)


@router.post("/courses/{course_id}/novelty/train")
def train_combiner(course_id: UUID, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    require_course(db, user, course_id, COORDINATOR)
    return training.train(db, course_id)


@router.post("/courses/{course_id}/novelty/evaluate-test")
def evaluate_on_test(course_id: UUID, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    """Held-out evaluation. Every access is recorded on the model so the count can be reported."""
    require_course(db, user, course_id, COORDINATOR)
    return training.evaluate_test(db, course_id)


@router.get("/courses/{course_id}/novelty/model")
def model_card(course_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_course(db, user, course_id)
    return training.model_card(db, course_id)


# --- Reviewer-adapted ranker (plan §3.1) ---


@router.post("/courses/{course_id}/ranker/train")
def train_ranker(course_id: UUID, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """Fit and compare against both floors. The simpler model ships if LightGBM does not win."""
    require_course(db, user, course_id, COORDINATOR)
    return ranker_training.train(db, course_id)


@router.get("/courses/{course_id}/ranker/model")
def ranker_card(course_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_course(db, user, course_id)
    return ranker_training.model_card(db, course_id)
