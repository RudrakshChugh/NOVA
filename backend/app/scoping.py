"""Per-subject authorisation (plan.md §14.1).

A role used to be platform-wide: any coordinator could see and change anything. The institute runs
many subjects with a different coordinator for each, so what somebody may do is now a property of
(person, subject) and lives in `course_members`. `users.role` keeps only what is genuinely
platform-wide — whether the account has been admitted at all, and whether it may create subjects
and admit others.

Three rules this module keeps:

  * **A non-member gets 404, not 403.** 403 confirms the object exists, which tells one
    department's coordinator that another department's paper is there to be asked about. The
    objects of a subject somebody is not in are invisible, not merely forbidden.
  * **An admin is a coordinator of every subject.** Admins create subjects and name their
    coordinators, so a subject an admin cannot enter is a subject nobody can repair.
  * **Scope is resolved from the object, never from the request.** A blueprint id names its
    course; the caller does not get to say which course it belongs to.
"""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Blueprint,
    CandidateQuestion,
    Course,
    CourseMember,
    GenerationRun,
    Material,
    PYQPaper,
    QuestionBankEntry,
    User,
)

# Platform standing.
PENDING, MEMBER, ADMIN = "pending", "member", "admin"
PLATFORM_ROLES = (PENDING, MEMBER, ADMIN)

# Standing on one subject.
REVIEWER, COORDINATOR = "reviewer", "coordinator"
COURSE_ROLES = (REVIEWER, COORDINATOR)


def is_admin(user: User) -> bool:
    return user.role == ADMIN


def course_role(db: Session, user: User, course_id: UUID) -> str | None:
    """The caller's role on one subject, or None when they are not in it."""
    if is_admin(user):
        return COORDINATOR
    membership = db.scalar(
        select(CourseMember).where(
            CourseMember.course_id == course_id, CourseMember.user_id == user.user_id
        )
    )
    return membership.role if membership is not None else None


def visible_courses(db: Session, user: User) -> list[Course]:
    """Every subject the caller may open. An admin sees all of them."""
    query = select(Course).order_by(Course.code)
    if not is_admin(user):
        query = query.join(CourseMember, CourseMember.course_id == Course.course_id).where(
            CourseMember.user_id == user.user_id
        )
    return list(db.scalars(query).all())


def _guard(db: Session, user: User, course_id: UUID, roles: tuple[str, ...], what: str) -> str:
    """The one place a scope decision is made. Returns the caller's role on that subject."""
    role = course_role(db, user, course_id)
    if role is None:
        raise HTTPException(404, f"{what} not found")
    if roles and role not in roles:
        raise HTTPException(403, f"requires {' or '.join(roles)} on this course")
    return role


# --- entry points, one per kind of object the API addresses ---------------------------------


def require_course(db: Session, user: User, course_id: UUID, *roles: str) -> Course:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(404, "course not found")
    _guard(db, user, course_id, roles, "course")
    return course


def require_material(db: Session, user: User, material_id: UUID, *roles: str) -> Material:
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(404, "material not found")
    _guard(db, user, material.course_id, roles, "material")
    return material


def require_paper(db: Session, user: User, paper_id: UUID, *roles: str) -> PYQPaper:
    paper = db.get(PYQPaper, paper_id)
    if paper is None:
        raise HTTPException(404, "paper not found")
    _guard(db, user, paper.course_id, roles, "paper")
    return paper


def require_blueprint(db: Session, user: User, blueprint_id: UUID, *roles: str) -> Blueprint:
    bp = db.get(Blueprint, blueprint_id)
    if bp is None:
        raise HTTPException(404, "blueprint not found")
    _guard(db, user, bp.course_id, roles, "blueprint")
    return bp


def require_run(db: Session, user: User, run_id: UUID, *roles: str) -> GenerationRun:
    run = db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    _guard(db, user, run.blueprint.course_id, roles, "run")
    return run


def require_candidate(db: Session, user: User, question_id: UUID, *roles: str) -> CandidateQuestion:
    candidate = db.get(CandidateQuestion, question_id)
    if candidate is None:
        raise HTTPException(404, "candidate not found")
    _guard(db, user, candidate.run.blueprint.course_id, roles, "candidate")
    return candidate


def require_entry(db: Session, user: User, entry_id: UUID, *roles: str) -> QuestionBankEntry:
    entry = db.get(QuestionBankEntry, entry_id)
    if entry is None:
        raise HTTPException(404, "entry not found")
    _guard(db, user, entry.question.run.blueprint.course_id, roles, "entry")
    return entry


def course_ids_for(db: Session, user: User) -> list[UUID] | None:
    """The subjects to filter an unscoped listing by, or None for an admin, who sees everything."""
    if is_admin(user):
        return None
    return [
        row
        for row in db.scalars(
            select(CourseMember.course_id).where(CourseMember.user_id == user.user_id)
        ).all()
    ]
