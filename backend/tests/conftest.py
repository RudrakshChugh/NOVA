"""Shared fixtures. Tests run against a real PostgreSQL/pgvector database (`aqp_test`) so
ingestion, vector search and every table write are exercised; only the LLM is stubbed."""

import os
import textwrap

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/aqp_test")

# Settings also read backend/.env, so the suite would otherwise inherit whatever the developer has
# configured there — pointing at a real identity provider, or with email sign-in switched off.
# Environment variables outrank the file, so the two the suite depends on are pinned here.
# The evidence check is one more model call per question. The stubs in these suites count
# their calls and return one fixed reply, so it is off here and switched on by the tests
# that are about it.
os.environ["CLAIM_CHECK_ENABLED"] = "false"
os.environ["DEV_LOGIN"] = "true"
# The suite must never reach a real model. With a hosted key in backend/.env, the one test that
# starts a run without stubbing the adapter would otherwise call the provider for real — spending
# the developer's quota and hanging the suite on a rate limit. Pointed at a port nothing listens
# on, that run fails immediately, which is all the test needs.
os.environ["LLM_BACKEND"] = "ollama"
os.environ["OLLAMA_URL"] = "http://127.0.0.1:9"
os.environ["GROQ_API_KEY"] = ""
os.environ["OIDC_ISSUER"] = ""

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Course, CourseMember, User  # noqa: E402
from app.scoping import COORDINATOR, REVIEWER  # noqa: E402
from sqlalchemy import select  # noqa: E402


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    from sqlalchemy.engine import make_url

    from app.config import settings

    # Derived from DATABASE_URL rather than written out, so the suite runs against whatever
    # Postgres the developer points it at — a different port, user or password — instead of
    # only the one address that used to be hard-coded here.
    target = make_url(settings.database_url)
    admin = target.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target.database,)).fetchone()
        if exists is None:
            conn.execute(f'CREATE DATABASE "{target.database}"')

    settings.storage_dir = tmp_path_factory.mktemp("storage")
    # keep a test run from overwriting the models the application serves
    models = tmp_path_factory.mktemp("models")
    settings.novelty_model_path = models / "novelty_combiner.json"
    settings.ranker_model_path = models / "ranker.json"
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        # Every data route requires a signed-in user, so the client carries a coordinator session
        # by default. A request that passes its own Authorization header overrides this, which is
        # how the role-permission tests still exercise a reviewer or an unprivileged user.
        token = c.post("/auth/login", json={"email": "coordinator@example.edu"}).json()["token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="session")
def new_course():
    """A course of this test's own, so one test module's corpus cannot change another's run."""

    def make(code: str, title: str = "Isolated Course", members: bool = True) -> str:
        with SessionLocal() as db:
            course = Course(code=code, title=title)
            db.add(course)
            db.flush()
            # A subject created in the app is created by an administrator who names its
            # people. `members=False` makes a subject the demo accounts are *not* in, which
            # is what the cross-subject tests need.
            if members:
                for email, role in (
                    ("coordinator@example.edu", COORDINATOR),
                    ("reviewer1@example.edu", REVIEWER),
                    ("reviewer2@example.edu", REVIEWER),
                ):
                    user = db.scalar(select(User).where(User.email == email))
                    if user is not None:
                        db.add(CourseMember(
                            course_id=course.course_id, user_id=user.user_id, role=role,
                            reason="test fixture",
                        ))
            db.commit()
            return str(course.course_id)

    return make


def login(client, email: str) -> dict[str, str]:
    token = client.post("/auth/login", json={"email": email}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def first_passage_sentence(user_prompt: str) -> str:
    """The opening sentence of retrieved passage [1], so a stub cites text that is really there."""
    body = user_prompt.split("Course passages:\n", 1)[1]
    passage = body.split("\n\n")[0].split(") ", 1)[1]
    head = passage.split(". ", 1)[0]
    return head if head == passage else f"{head}."


def upload_material(client, course_id, path, headers, name=None):
    """Upload one file and return its finished ingestion job.

    Upload answers 202 with the file still queued; TestClient runs the background task before it
    returns, so the job row is already final by the time this reads it back.
    """
    from pathlib import Path

    path = Path(path)
    with path.open("rb") as f:
        r = client.post(
            f"/courses/{course_id}/materials",
            files={"files": (name or path.name, f, "application/pdf")}, headers=headers,
        )
    assert r.status_code == 202, r.text
    job_id = r.json()[0]["job_id"]
    jobs = client.get(f"/courses/{course_id}/ingestion-jobs", headers=headers).json()
    return next(j for j in jobs if j["job_id"] == job_id)


def write_pdf(path, text: str):
    """A one-page PDF with wrapped text, so ingestion is exercised on a real file."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    canvas_obj = canvas.Canvas(str(path), pagesize=A4)
    textobject = canvas_obj.beginText(40, A4[1] - 60)
    textobject.setFont("Helvetica", 11)
    for line in textwrap.wrap(text, width=95):
        textobject.textLine(line)
    canvas_obj.drawText(textobject)
    canvas_obj.save()
    return path
