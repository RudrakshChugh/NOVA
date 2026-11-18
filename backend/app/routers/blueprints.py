from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import Blueprint, BlueprintConstraint, CourseOutcome, User
from ..schemas import BlueprintOut, ConstraintIn, ValidationOut
from ..scoping import COORDINATOR, require_blueprint, require_course
from ..config import settings
from ..services import compliance, orchestrator

router = APIRouter(tags=["blueprints"])

def _out(db: Session, bp: Blueprint) -> BlueprintOut:
    """A blueprint plus the version it descends from, so the lineage is legible on screen
    rather than being a bare id."""
    out = BlueprintOut.model_validate(bp, from_attributes=True)
    if bp.revised_from is not None:
        parent = db.get(Blueprint, bp.revised_from)
        out.revised_from_version = parent.version if parent else None
    return out


def level_warnings(constraints) -> list[str]:
    """Rows that ask for more than the outcome they assess is stated at.

    An outcome written as "Explain ..." commits the course to comprehension. A row that assesses
    it at `create` is examining something the outcome never promised, which is the commonest way
    a paper drifts from its outcomes while every row still names one. The level of an outcome is
    read from its leading verb with the same classifier the compliance check uses on questions.

    Advisory only. A coordinator may have a reason, an outcome may open with a verb the
    classifier does not know, and a row *below* its outcome's level is ordinary — a paper needs
    recall questions on outcomes stated higher up.
    """
    out: list[str] = []
    for c in constraints:
        stated = compliance.outcome_bloom_level(c.course_outcome)
        if stated is None or c.bloom_level not in compliance.BLOOM_CUES:
            continue
        if compliance.bloom_rank(c.bloom_level) > compliance.bloom_rank(stated):
            code = c.course_outcome.split()[0] if c.course_outcome.split() else "its outcome"
            out.append(
                f"Row {c.position + 1} is set at \u201c{c.bloom_level}\u201d, but {code} is stated at "
                f"\u201c{stated}\u201d. The question would examine more than the outcome commits to."
            )
    return out


def twin_warnings(constraints) -> list[str]:
    """Rows that ask for the same thing twice.

    Measured on real runs: two rows with the same outcome, concept and Bloom level reliably come
    back as one task set in two scenarios ("identify the entities and flows of a Level-0 DFD"
    for a bookstore, then for a library). The wording differs enough that the duplicate score
    stays under the line, so nothing downstream catches it. The remedy is in the blueprint —
    a narrower concept or a different level on one of the rows — which is why it is said here.
    """
    seen: dict[tuple, int] = {}
    out: list[str] = []
    for c in constraints:
        key = (c.outcome_id or c.course_outcome.strip().lower(), c.concept.strip().lower(), c.bloom_level)
        if key in seen:
            out.append(
                f"Rows {seen[key] + 1} and {c.position + 1} share an outcome, a concept (\u201c{c.concept}\u201d) "
                "and a Bloom level. Each will be given a different form of task and a different "
                "share of the passages, but a narrower concept on one of them makes a better paper."
            )
        else:
            seen[key] = c.position
    return out


def coverage_warnings(db: Session, bp: Blueprint) -> list[str]:
    """Rows whose concept the indexed material does not cover.

    Said here, while the blueprint can still be changed, rather than discovered when a run skips
    the row. Silent when nothing is indexed yet: that is a different problem with its own
    message, and a warning on every row would bury it."""
    out: list[str] = []
    for c in bp.constraints:
        score = orchestrator.coverage(db, bp.course_id, c)
        if orchestrator.is_uncovered(score):
            out.append(
                f"Row {c.position + 1}: the course material has little on \u201c{c.concept}\u201d "
                f"(best match {score:.2f}, needs {settings.retrieval_min_similarity:.2f}). "
                "A run will skip this row rather than invent a question."
            )
    return out


def _validate(db: Session, bp: Blueprint) -> ValidationOut:
    issues: list[str] = []
    outcomes: dict[str, int] = {}
    if not bp.constraints:
        issues.append("blueprint has no constraints")
    for c in bp.constraints:
        outcomes[c.course_outcome] = outcomes.get(c.course_outcome, 0) + c.marks

    # Coverage is only meaningful once the subject has stated its outcomes. Until then the map
    # above is a tally of whatever strings were typed, which is why §14.6 exists.
    stated = list(db.scalars(
        select(CourseOutcome).where(CourseOutcome.course_id == bp.course_id)
        .order_by(CourseOutcome.position)
    ).all())
    uncovered: list[str] = []
    if stated:
        allocated = {c.outcome_id for c in bp.constraints if c.outcome_id is not None}
        uncovered = [f"{o.code} {o.text}" for o in stated if o.outcome_id not in allocated]
        unlinked = sum(1 for c in bp.constraints if c.outcome_id is None)
        if unlinked:
            issues.append(
                f"{unlinked} row(s) are not linked to one of this subject\u2019s course outcomes; "
                "choose the outcome rather than typing it, so coverage can be counted"
            )
    return ValidationOut(
        ok=not issues, issues=issues, total_marks=sum(outcomes.values()),
        outcomes=outcomes, uncovered=uncovered,
        warnings=level_warnings(bp.constraints) + twin_warnings(bp.constraints) + coverage_warnings(db, bp),
    )


@router.get("/courses/{course_id}/blueprints", response_model=list[BlueprintOut])
def list_blueprints(course_id: UUID, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    require_course(db, user, course_id)
    rows = db.scalars(select(Blueprint).where(Blueprint.course_id == course_id).order_by(Blueprint.frozen_at.nulls_first(), Blueprint.version)).all()
    return [_out(db, bp) for bp in rows]


@router.post("/courses/{course_id}/blueprints", response_model=BlueprintOut, status_code=201)
def create_blueprint(course_id: UUID, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    require_course(db, user, course_id, COORDINATOR)
    bp = Blueprint(course_id=course_id)
    db.add(bp)
    db.commit()
    return bp


@router.get("/blueprints/{blueprint_id}", response_model=BlueprintOut)
def get_blueprint(blueprint_id: UUID, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    return _out(db, require_blueprint(db, user, blueprint_id))


@router.put("/blueprints/{blueprint_id}/constraints", response_model=BlueprintOut)
def put_constraints(blueprint_id: UUID, rows: list[ConstraintIn], db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    bp = require_blueprint(db, user, blueprint_id, COORDINATOR)
    if bp.frozen_at is not None:
        raise HTTPException(409, "blueprint is frozen; create a new draft to change it")

    # A row that names an outcome takes its wording from the outcome itself, so one outcome
    # cannot be spelt three ways across a blueprint, and the compliance check and the coverage
    # map are reading the same words.
    stated = {
        o.outcome_id: o for o in db.scalars(
            select(CourseOutcome).where(CourseOutcome.course_id == bp.course_id)
        ).all()
    }
    built: list[BlueprintConstraint] = []
    for i, r in enumerate(rows):
        values = r.model_dump()
        outcome_id = values.get("outcome_id")
        if outcome_id is not None:
            outcome = stated.get(outcome_id)
            if outcome is None:
                raise HTTPException(422, f"row {i + 1}: that outcome is not one of this course\u2019s")
            values["course_outcome"] = f"{outcome.code} {outcome.text}"
        built.append(BlueprintConstraint(position=i, **values))
    bp.constraints = built
    db.commit()
    db.refresh(bp)
    return bp


@router.delete("/blueprints/{blueprint_id}", status_code=204)
def delete_blueprint(blueprint_id: UUID, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    """Discard a draft. A frozen blueprint is never removable: published questions record the
    version they were written for, and runs are tied to it, so deleting one would orphan the
    provenance the bank depends on."""
    bp = require_blueprint(db, user, blueprint_id, COORDINATOR)
    if bp.frozen_at is not None:
        raise HTTPException(409, "a frozen blueprint cannot be deleted")
    db.delete(bp)
    db.commit()


@router.post("/blueprints/{blueprint_id}/revise", response_model=BlueprintOut, status_code=201)
def revise_blueprint(blueprint_id: UUID, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    """Copy a frozen version into a new draft.

    This is what "edit a frozen blueprint" has to mean. Runs, candidates and compliance
    reports all reference `constraint_id`, and the evaluation claims of Section 13 rest on
    those references still meaning what they meant at generation time. Editing the frozen rows
    would silently rewrite that history, so the frozen version and everything pointing at it
    are left exactly as they are and the change happens in a copy.
    """
    source = require_blueprint(db, user, blueprint_id, COORDINATOR)
    if source.frozen_at is None:
        raise HTTPException(409, "this blueprint is still a draft; edit it directly")
    draft = Blueprint(course_id=source.course_id, revised_from=source.blueprint_id)
    draft.constraints = [
        BlueprintConstraint(
            position=c.position, course_outcome=c.course_outcome, outcome_id=c.outcome_id,
            concept=c.concept, bloom_level=c.bloom_level, marks=c.marks,
            question_type=c.question_type,
        )
        for c in source.constraints
    ]
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return _out(db, draft)


@router.post("/blueprints/{blueprint_id}/validate", response_model=ValidationOut)
def validate_blueprint(blueprint_id: UUID, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    return _validate(db, require_blueprint(db, user, blueprint_id))


@router.post("/blueprints/{blueprint_id}/freeze", response_model=BlueprintOut)
def freeze_blueprint(blueprint_id: UUID, db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    bp = require_blueprint(db, user, blueprint_id, COORDINATOR)
    if bp.frozen_at is not None:
        raise HTTPException(409, "already frozen")
    result = _validate(db, bp)
    if not result.ok:
        raise HTTPException(422, {"issues": result.issues})
    latest = db.scalar(select(func.max(Blueprint.version)).where(Blueprint.course_id == bp.course_id)) or 0
    bp.version, bp.frozen_at = latest + 1, datetime.now(timezone.utc)
    db.commit()
    db.refresh(bp)
    return bp
