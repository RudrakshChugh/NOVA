"""Evaluation harness (plan §3.3): launch baseline and ablation arms, serve a blinded review
queue, and report every metric with its interval."""

import random
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import User
from ..schemas import CandidateOut, StudyLaunchIn
from ..scoping import COORDINATOR, REVIEWER, require_blueprint, require_candidate
from ..services import orchestrator
from ..services.evaluation import harness, report
from ..services.evaluation.arms import ARMS
from .runs import candidate_out

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.get("/arms")
def list_arms():
    """The pinned configuration of every baseline and ablation arm."""
    return {
        label: {
            "kind": arm.kind, "description": arm.description, "retrieval": arm.retrieval,
            "full_material": arm.full_material, "blueprint_in_prompt": arm.blueprint_in_prompt,
            "enforce_compliance": arm.enforce_compliance, "enforce_novelty": arm.enforce_novelty,
            "rank": arm.rank,
        }
        for label, arm in ARMS.items()
    }


@router.post("/blueprints/{blueprint_id}/study", status_code=202)
def launch_study(blueprint_id: UUID, body: StudyLaunchIn, background: BackgroundTasks,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Run every named arm once per seed against the same frozen blueprint."""
    bp = require_blueprint(db, user, blueprint_id, COORDINATOR)
    try:
        planned = harness.launch(db, bp, body.arms, body.seeds)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    for item in planned:
        background.add_task(orchestrator.execute_run, item.run.run_id)
    return {
        "blueprint_id": str(blueprint_id),
        "launched": [
            {"arm": item.arm.label, "seed": item.seed, "run_id": str(item.run.run_id)}
            for item in planned
        ],
        "seeds": harness.seeds_for(body.seeds),
    }


@router.get("/blueprints/{blueprint_id}/study")
def study_status(blueprint_id: UUID, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    require_blueprint(db, user, blueprint_id)
    return harness.status(db, blueprint_id)


@router.get("/blueprints/{blueprint_id}/queue", response_model=list[CandidateOut])
def blinded_queue(blueprint_id: UUID, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    """Every candidate of the study this reviewer has not yet judged, shuffled and unlabelled.

    Reviewer-only, deliberately, where the rest of the workflow also admits a coordinator: a
    coordinator can reveal which arm produced a question, so letting one judge through this queue
    would make the blinding unenforceable by the same person it is meant to constrain.

    The arm that produced a question is never part of this response, and the order is randomised
    per reviewer, so a reviewer cannot tell which system wrote what (plan §3.3.4). The shuffle is
    seeded by reviewer and blueprint so a reload keeps the same order rather than re-randomising
    half-way through a sitting.
    """
    require_blueprint(db, user, blueprint_id, REVIEWER)
    candidates = [c for run in harness.study_runs(db, blueprint_id) for c in run.candidates]
    pending = [
        c for c in candidates
        if c.stem and not any(r.reviewer_id == user.user_id for r in c.reviews)
    ]
    random.Random(f"{user.user_id}:{blueprint_id}").shuffle(pending)
    return [candidate_out(c, reveal_arm=False) for c in pending]


@router.get("/blueprints/{blueprint_id}/report")
def evaluation_report(blueprint_id: UUID, db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
    """Every metric of plan §3.3.6 for this study, each with its interval."""
    require_blueprint(db, user, blueprint_id)
    return report.build(db, blueprint_id)


@router.get("/candidates/{question_id}/arm")
def reveal_arm(question_id: UUID, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    """Which arm produced a question. Coordinator-only, so the label stays out of review."""
    candidate = require_candidate(db, user, question_id, COORDINATOR)
    return {"question_id": str(question_id), "arm_label": candidate.arm_label}
