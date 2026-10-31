from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_role
from ..db import get_db
from ..models import Blueprint, BlueprintConstraint, Course
from ..schemas import BlueprintOut, ConstraintIn, ValidationOut

router = APIRouter(tags=["blueprints"])

# target_difficulty is not checkable on a candidate (compliance.py); it is checked here for
# consistency with the Bloom level of the same constraint row.
DIFFICULTY_BLOOM = {
    "easy": {"remember", "understand"},
    "medium": {"apply", "analyze"},
    "hard": {"evaluate", "create"},
}


def _get(db: Session, blueprint_id: UUID) -> Blueprint:
    bp = db.get(Blueprint, blueprint_id)
    if bp is None:
        raise HTTPException(404, "blueprint not found")
    return bp


def _validate(bp: Blueprint) -> ValidationOut:
    issues: list[str] = []
    outcomes: dict[str, int] = {}
    if not bp.constraints:
        issues.append("blueprint has no constraints")
    for i, c in enumerate(bp.constraints, start=1):
        outcomes[c.course_outcome] = outcomes.get(c.course_outcome, 0) + c.marks
        if c.bloom_level not in DIFFICULTY_BLOOM[c.target_difficulty]:
            issues.append(
                f"row {i}: target_difficulty '{c.target_difficulty}' is inconsistent with bloom_level '{c.bloom_level}' "
                f"(expected one of {sorted(DIFFICULTY_BLOOM[c.target_difficulty])})"
            )
    return ValidationOut(ok=not issues, issues=issues, total_marks=sum(outcomes.values()), outcomes=outcomes)


@router.get("/courses/{course_id}/blueprints", response_model=list[BlueprintOut])
def list_blueprints(course_id: UUID, db: Session = Depends(get_db)):
    return db.scalars(select(Blueprint).where(Blueprint.course_id == course_id).order_by(Blueprint.frozen_at.nulls_first(), Blueprint.version)).all()


@router.post("/courses/{course_id}/blueprints", response_model=BlueprintOut, status_code=201, dependencies=[Depends(require_role("coordinator"))])
def create_blueprint(course_id: UUID, db: Session = Depends(get_db)):
    if db.get(Course, course_id) is None:
        raise HTTPException(404, "course not found")
    bp = Blueprint(course_id=course_id)
    db.add(bp)
    db.commit()
    return bp


@router.get("/blueprints/{blueprint_id}", response_model=BlueprintOut)
def get_blueprint(blueprint_id: UUID, db: Session = Depends(get_db)):
    return _get(db, blueprint_id)


@router.put("/blueprints/{blueprint_id}/constraints", response_model=BlueprintOut, dependencies=[Depends(require_role("coordinator"))])
def put_constraints(blueprint_id: UUID, rows: list[ConstraintIn], db: Session = Depends(get_db)):
    bp = _get(db, blueprint_id)
    if bp.frozen_at is not None:
        raise HTTPException(409, "blueprint is frozen; create a new draft to change it")
    bp.constraints = [BlueprintConstraint(position=i, **r.model_dump()) for i, r in enumerate(rows)]
    db.commit()
    db.refresh(bp)
    return bp


@router.delete("/blueprints/{blueprint_id}", status_code=204,
               dependencies=[Depends(require_role("coordinator"))])
def delete_blueprint(blueprint_id: UUID, db: Session = Depends(get_db)):
    """Discard a draft. A frozen blueprint is never removable: published questions record the
    version they were written for, and runs are tied to it, so deleting one would orphan the
    provenance the bank depends on."""
    bp = db.get(Blueprint, blueprint_id)
    if bp is None:
        raise HTTPException(404, "blueprint not found")
    if bp.frozen_at is not None:
        raise HTTPException(409, "a frozen blueprint cannot be deleted")
    db.delete(bp)
    db.commit()


@router.post("/blueprints/{blueprint_id}/validate", response_model=ValidationOut)
def validate_blueprint(blueprint_id: UUID, db: Session = Depends(get_db)):
    return _validate(_get(db, blueprint_id))


@router.post("/blueprints/{blueprint_id}/freeze", response_model=BlueprintOut, dependencies=[Depends(require_role("coordinator"))])
def freeze_blueprint(blueprint_id: UUID, db: Session = Depends(get_db)):
    bp = _get(db, blueprint_id)
    if bp.frozen_at is not None:
        raise HTTPException(409, "already frozen")
    result = _validate(bp)
    if not result.ok:
        raise HTTPException(422, {"issues": result.issues})
    latest = db.scalar(select(func.max(Blueprint.version)).where(Blueprint.course_id == bp.course_id)) or 0
    bp.version, bp.frozen_at = latest + 1, datetime.now(timezone.utc)
    db.commit()
    db.refresh(bp)
    return bp
