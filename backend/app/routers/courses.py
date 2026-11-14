"""Subjects, their course outcomes, and who may do what on each one (plan.md §14.1, §14.6).

A deployment used to serve exactly one course, seeded from configuration. It now holds one row per
subject, created here by an administrator, who also names the subject's coordinator. That
coordinator then grants reviewers on their own subject and nowhere else.
"""

import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user, require_admin
from ..db import get_db
from ..models import BlueprintConstraint, Course, CourseMember, CourseOutcome, User
from ..schemas import (
    CourseIn,
    CourseMemberOut,
    CourseOut,
    ExtractedOutcomeOut,
    MemberIn,
    OutcomeIn,
    OutcomeOut,
    SyllabusReadOut,
)
from ..scoping import COORDINATOR, COURSE_ROLES, MEMBER, course_role, is_admin, require_course, visible_courses
from ..services import syllabus

router = APIRouter(tags=["courses"])


def _member_out(membership: CourseMember) -> CourseMemberOut:
    return CourseMemberOut(
        user_id=membership.user.user_id,
        email=membership.user.email,
        name=membership.user.name,
        role=membership.role,
        granted_at=membership.granted_at,
        reason=membership.reason or None,
    )


def _course_out(db: Session, course: Course, user: User) -> CourseOut:
    return CourseOut(
        course_id=course.course_id, code=course.code, title=course.title,
        your_role=course_role(db, user, course.course_id),
    )


@router.get("/courses", response_model=list[CourseOut])
def list_courses(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Only the subjects this person is in, each with the role they hold on it. An
    administrator sees all of them, as a coordinator."""
    return [_course_out(db, c, user) for c in visible_courses(db, user)]


@router.post("/courses", response_model=CourseOut, status_code=201)
def create_course(body: CourseIn, db: Session = Depends(get_db),
                  user: User = Depends(require_admin)):
    if db.scalar(select(Course).where(Course.code == body.code.strip())) is not None:
        raise HTTPException(409, f"a course with code {body.code.strip()} already exists")
    course = Course(code=body.code.strip(), title=body.title.strip())
    db.add(course)
    db.commit()
    return _course_out(db, course, user)


@router.get("/courses/{course_id}/members", response_model=list[CourseMemberOut])
def list_members(course_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if course_role(db, user, course_id) is None:
        raise HTTPException(404, "course not found")
    rows = db.scalars(
        select(CourseMember).where(CourseMember.course_id == course_id).order_by(CourseMember.role)
    ).all()
    return [_member_out(m) for m in rows]


@router.post("/courses/{course_id}/members", response_model=CourseMemberOut, status_code=201)
def grant_membership(course_id: UUID, body: MemberIn, db: Session = Depends(get_db),
                     actor: User = Depends(current_user)):
    """Put somebody on this subject, or change the role they hold on it.

    Coordinator-only, and only on this subject: a coordinator of one subject has no standing on
    another, which is the whole point of the table.
    """
    if course_role(db, actor, course_id) != COORDINATOR:
        # 404 rather than 403, so a coordinator of another subject cannot confirm this one exists.
        raise HTTPException(404, "course not found")
    if body.role not in COURSE_ROLES:
        raise HTTPException(422, f"role must be one of {', '.join(COURSE_ROLES)}")

    target = db.get(User, body.user_id)
    if target is None:
        raise HTTPException(404, "unknown user")
    if target.role not in (MEMBER, "admin"):
        raise HTTPException(409, "that account has not been admitted to the platform yet")

    membership = db.scalar(
        select(CourseMember).where(
            CourseMember.course_id == course_id, CourseMember.user_id == target.user_id
        )
    )
    if membership is None:
        membership = CourseMember(course_id=course_id, user_id=target.user_id)
        db.add(membership)
    membership.role = body.role
    membership.granted_by = actor.user_id
    membership.reason = body.reason or "granted on the subject's People screen"
    db.commit()
    db.refresh(membership)
    return _member_out(membership)


@router.delete("/courses/{course_id}/members/{user_id}", status_code=204)
def revoke_membership(course_id: UUID, user_id: UUID, db: Session = Depends(get_db),
                      actor: User = Depends(current_user)):
    if course_role(db, actor, course_id) != COORDINATOR:
        raise HTTPException(404, "course not found")
    if user_id == actor.user_id and not is_admin(actor):
        # The same reasoning as refusing to change your own platform role: the last coordinator of
        # a subject removing themselves leaves it with nobody who can add one back.
        raise HTTPException(400, "you cannot remove yourself from a course you coordinate")
    membership = db.scalar(
        select(CourseMember).where(
            CourseMember.course_id == course_id, CourseMember.user_id == user_id
        )
    )
    if membership is None:
        raise HTTPException(404, "not a member of this course")
    db.delete(membership)
    db.commit()


# --- course outcomes (plan.md §14.6) ----------------------------------------


@router.post("/courses/{course_id}/syllabus", response_model=SyllabusReadOut)
def read_syllabus(course_id: UUID, file: UploadFile, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """Read the outcomes out of a syllabus and hand them back **without storing anything**.

    The coordinator sees what was found, corrects it, and saves it with the next call. A parser
    that writes straight to the database is a parser whose mistakes are discovered later, by
    somebody writing a blueprint against an outcome the syllabus does not contain.
    """
    require_course(db, user, course_id, COORDINATOR)
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in (".pdf", ".docx"):
        raise HTTPException(400, "only .pdf and .docx are accepted")

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        path = Path(tmp.name)
    try:
        found = syllabus.extract_outcomes(syllabus.read_text(path, ext.lstrip(".")))
    finally:
        path.unlink(missing_ok=True)

    if not found:
        raise HTTPException(
            422,
            "No course outcomes were recognised in that document. They are usually written as "
            "'CO1: ...' or listed under a 'Course Outcomes' heading. Add them by hand instead.",
        )
    unsure = [o for o in found if o.confidence < 1.0]
    note = (
        f"{len(found)} outcomes read from {file.filename}. "
        + (f"{len(unsure)} came from an unlabelled list, so check them first. "
           if unsure else "Every one carried its own CO label. ")
        + "Nothing is saved until you save it."
    )
    return SyllabusReadOut(
        filename=file.filename or "syllabus",
        outcomes=[ExtractedOutcomeOut(code=o.code, text=o.text, confidence=o.confidence) for o in found],
        note=note,
    )


@router.get("/courses/{course_id}/outcomes", response_model=list[OutcomeOut])
def list_outcomes(course_id: UUID, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    require_course(db, user, course_id)
    return db.scalars(
        select(CourseOutcome).where(CourseOutcome.course_id == course_id)
        .order_by(CourseOutcome.position)
    ).all()


@router.put("/courses/{course_id}/outcomes", response_model=list[OutcomeOut])
def put_outcomes(course_id: UUID, rows: list[OutcomeIn], source_filename: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Save the list, and keep saving it: a syllabus is revised and an outcome gets reworded.

    Rows are matched by code rather than replaced wholesale, so an outcome that keeps its code
    keeps its identity — and every blueprint already pointing at it keeps pointing at it. Only a
    code that disappears entirely is removed, and the constraints that referenced it fall back to
    the text they were written with rather than breaking.
    """
    require_course(db, user, course_id, COORDINATOR)
    codes = [r.code.strip().upper() for r in rows]
    if len(set(codes)) != len(codes):
        raise HTTPException(422, "two outcomes share a code; each code must appear once")

    existing = {
        o.code: o for o in db.scalars(
            select(CourseOutcome).where(CourseOutcome.course_id == course_id)
        ).all()
    }
    kept: list[CourseOutcome] = []
    for position, (code, row) in enumerate(zip(codes, rows)):
        outcome = existing.pop(code, None)
        if outcome is None:
            outcome = CourseOutcome(course_id=course_id, code=code)
            db.add(outcome)
        outcome.text = row.text.strip()
        outcome.position = position
        outcome.confidence = row.confidence
        if source_filename:
            outcome.source_filename = source_filename
        kept.append(outcome)

    for dropped in existing.values():
        db.query(BlueprintConstraint).filter(
            BlueprintConstraint.outcome_id == dropped.outcome_id
        ).update({"outcome_id": None}, synchronize_session=False)
        db.delete(dropped)

    db.commit()
    for outcome in kept:
        db.refresh(outcome)
    return kept
