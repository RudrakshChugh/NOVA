from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .auth import ADMIN, MEMBER, require_active
from .config import settings
from .db import SessionLocal, init_db
from .models import Course, CourseMember, DuplicatePair, ModelVersion, RoleChange, User
from .routers import (
    auth, bank, blueprints, courses, evaluation, materials, novelty, papers, pyqs, reviews, runs,
    system,
)
from .services import orchestrator
from .scoping import COORDINATOR, REVIEWER

# Demo accounts for email sign-in. Each has a platform standing and a role on the seeded course,
# which are now two different things (plan.md §14.1).
SEED_USERS = [
    ("coordinator@example.edu", "Course Coordinator", ADMIN, COORDINATOR),
    ("reviewer1@example.edu", "Faculty Reviewer 1", MEMBER, REVIEWER),
    ("reviewer2@example.edu", "Faculty Reviewer 2", MEMBER, REVIEWER),
]


def backfill_memberships() -> None:
    """One-time translation of the platform-wide roles that preceded per-subject membership.

    Before subjects, `users.role` was `reviewer` or `coordinator` and meant the same thing
    everywhere. Deploying the split without this would leave every existing account holding a value
    that now satisfies nothing, which locks out the very people who were using the deployment. Each
    one keeps what it had, on every subject that exists at the time, and the translation is written
    to the role audit so it is not mistaken later for somebody's deliberate act.
    """
    with SessionLocal() as db:
        legacy = list(db.scalars(select(User).where(User.role.in_((REVIEWER, COORDINATOR)))).all())
        if not legacy:
            return
        existing_courses = list(db.scalars(select(Course)).all())
        for user in legacy:
            for course in existing_courses:
                already = db.scalar(
                    select(CourseMember).where(
                        CourseMember.course_id == course.course_id,
                        CourseMember.user_id == user.user_id,
                    )
                )
                if already is None:
                    db.add(CourseMember(
                        course_id=course.course_id, user_id=user.user_id, role=user.role,
                        reason="carried over from the platform-wide role that preceded subjects",
                    ))
            was = user.role
            # A coordinator ran the deployment, so it becomes an administrator; a reviewer was only
            # ever a reviewer, so it becomes an ordinary admitted member.
            user.role = ADMIN if was == COORDINATOR else MEMBER
            db.add(RoleChange(
                user_id=user.user_id, actor_id=None, from_role=was, to_role=user.role,
                reason="platform standing split from per-subject roles (plan.md §14.1)",
            ))
        db.commit()


def backfill_model_scope() -> None:
    """Attach labelled pairs and fitted models to the subject they were built for.

    They were platform-wide before subjects existed, which only worked because there was one
    subject. Where that is still true, the single subject is the answer and these rows stay
    usable; where it is not, the rows are left unattached rather than guessed at, because
    assigning one department's labelled pairs to another is worse than losing them.
    """
    with SessionLocal() as db:
        courses = list(db.scalars(select(Course)).all())
        if len(courses) != 1:
            return
        only = courses[0].course_id
        db.query(DuplicatePair).filter(DuplicatePair.course_id.is_(None)).update(
            {"course_id": only}, synchronize_session=False
        )
        db.query(ModelVersion).filter(ModelVersion.course_id.is_(None)).update(
            {"course_id": only}, synchronize_session=False
        )
        db.commit()


def seed() -> None:
    """A course is always ensured, so a fresh deployment has something to open. The demo users only
    exist where email sign-in is enabled; with an identity provider configured, accounts are created
    by signing in."""
    with SessionLocal() as db:
        course = db.scalar(select(Course))
        if course is None:
            course = Course(code=settings.course_code, title=settings.course_title)
            db.add(course)
            db.flush()
        if settings.dev_login:
            for email, name, standing, on_course in SEED_USERS:
                user = db.scalar(select(User).where(User.email == email))
                if user is None:
                    user = User(email=email, name=name, role=standing)
                    db.add(user)
                    db.flush()
                member = db.scalar(
                    select(CourseMember).where(
                        CourseMember.course_id == course.course_id,
                        CourseMember.user_id == user.user_id,
                    )
                )
                if member is None:
                    db.add(CourseMember(
                        course_id=course.course_id, user_id=user.user_id, role=on_course,
                        reason="seeded demo account",
                    ))
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    backfill_memberships()
    seed()
    backfill_model_scope()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    # Background work does not survive a restart, so anything still marked as under way at
    # startup was orphaned by one. Without this a single crash leaves a run "in progress"
    # forever and the interface never offers to start another.
    orchestrator.recover_interrupted_runs()
    materials.recover_interrupted_jobs()
    yield


app = FastAPI(title="Assessment-Quality Platform (prototype)", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.allowed_origins,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(auth.router)


@app.get("/health", tags=["system"])
def health() -> dict:
    """Liveness only, and deliberately unauthenticated: it says the process is up and nothing
    about what it holds. The detailed readiness report is `/system/status`, behind sign-in."""
    return {"status": "ok"}


# Everything else requires a signed-in account that has been admitted. Without this the read
# endpoints are public, which on a deployed instance would hand uploaded course material, generated
# candidates and the question bank to anyone with the URL. Per-subject scoping inside the routers
# applies on top of this: being admitted says nothing about which subjects you may open.
for r in (courses, materials, pyqs, blueprints, runs, reviews, bank, papers, novelty, evaluation, system):
    app.include_router(r.router, dependencies=[Depends(require_active)])
