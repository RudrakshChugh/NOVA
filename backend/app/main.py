from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .config import settings
from .auth import require_active
from .db import SessionLocal, init_db
from .models import Course, User
from .routers import auth, bank, blueprints, evaluation, materials, novelty, pyqs, reviews, runs

# Prototype scope (plan §2, §7): one course, one coordinator, two faculty reviewers.
SEED_USERS = [
    ("coordinator@example.edu", "Course Coordinator", "coordinator"),
    ("reviewer1@example.edu", "Faculty Reviewer 1", "reviewer"),
    ("reviewer2@example.edu", "Faculty Reviewer 2", "reviewer"),
]
SEED_COURSE = ("CS101", "Prototype Course")


def seed() -> None:
    with SessionLocal() as db:
        for email, name, role in SEED_USERS:
            if db.scalar(select(User).where(User.email == email)) is None:
                db.add(User(email=email, name=name, role=role))
        if db.scalar(select(Course)) is None:
            db.add(Course(code=SEED_COURSE[0], title=SEED_COURSE[1]))
        db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.dev_login:
        seed()  # demo users exist only where sign-in is by email
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Assessment-Quality Platform (prototype)", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.allowed_origins,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(auth.router)

# Everything else requires a signed-in user who has been granted a role. Without this the read
# endpoints are public, which on a deployed instance would hand uploaded course material, generated
# candidates and the question bank to anyone with the URL. Role checks inside the routers still
# apply on top of this.
for r in (materials, pyqs, blueprints, runs, reviews, bank, novelty, evaluation):
    app.include_router(r.router, dependencies=[Depends(require_active)])
