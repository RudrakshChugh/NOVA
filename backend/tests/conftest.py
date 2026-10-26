"""Shared fixtures. Tests run against a real PostgreSQL/pgvector database (`aqp_test`) so
ingestion, vector search and every table write are exercised; only the LLM is stubbed."""

import os
import textwrap

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/aqp_test")

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Course  # noqa: E402


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    with psycopg.connect("postgresql://postgres:postgres@localhost:5432/postgres", autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname='aqp_test'").fetchone() is None:
            conn.execute("CREATE DATABASE aqp_test")
    from app.config import settings

    settings.storage_dir = tmp_path_factory.mktemp("storage")
    # keep a test run from overwriting the models the application serves
    models = tmp_path_factory.mktemp("models")
    settings.novelty_model_path = models / "novelty_combiner.json"
    settings.ranker_model_path = models / "ranker.json"
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="session")
def new_course():
    """A course of this test's own, so one test module's corpus cannot change another's run."""

    def make(code: str, title: str = "Isolated Course") -> str:
        with SessionLocal() as db:
            course = Course(code=code, title=title)
            db.add(course)
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
