"""Per-subject scoping (plan.md §14.1).

Before subjects, a role meant the same thing everywhere, so a missing filter was a cosmetic bug.
It is now a disclosure between colleagues: one department's coordinator must not be able to read,
change or even confirm the existence of another department's material, drafts or question bank.

These tests therefore address every router with an object that really exists, from an account that
really has a role — just not on that subject — and require **404** rather than 403. A 403 answers
the question "is there a paper there to ask about?", which is itself the leak.
"""

import pytest
from sqlalchemy import select

from app.llm.base import GenerationResult
from app.services import orchestrator

from .conftest import login, upload_material, write_pdf

PASSAGE = (
    "Normalisation removes redundancy from a relational schema. "
    "First normal form requires every attribute to hold an atomic value. "
    "Second normal form removes partial dependencies on a composite key. "
    "Third normal form removes transitive dependencies between non-key attributes. "
    "Denormalisation reintroduces redundancy deliberately to make reads cheaper."
)


class PlainAdapter:
    """Always compliant, so these tests fail on scoping rather than on compliance."""

    backend_id = "stub:scoping"

    def generate(self, req):
        quote = PASSAGE.split(". ")[0] + "."
        passages = req.user.split("Course passages:\n", 1)[1]
        first = passages.split("\n\n")[0].split(") ", 1)[1]
        head = first.split(". ")[0] + "."
        return GenerationResult(
            data={
                "stem": "Explain how third normal form removes transitive dependencies.",
                "answer_key": (
                    "Third normal form removes transitive dependencies between non-key attributes, "
                    "so every non-key attribute depends on the key alone. A schema in third normal "
                    "form therefore stores each fact once, which is what makes an update safe to "
                    "apply in a single place rather than in several rows at once."
                ),
                "options": [],
                "citations": [{"passage": 1, "quote": head or quote}],
            },
            model_version="stub", temperature=None, seed=None,
        )


@pytest.fixture(scope="module")
def foreign(client, new_course, tmp_path_factory):
    """A subject nobody but an administrator is in, populated through the administrator."""
    monkey = pytest.MonkeyPatch()
    monkey.setattr(orchestrator, "get_adapter", lambda: PlainAdapter())
    admin = login(client, "coordinator@example.edu")
    course_id = new_course("FOREIGN", "Another Department", members=False)

    pdf = write_pdf(tmp_path_factory.mktemp("foreign") / "unit.pdf", PASSAGE)
    job = upload_material(client, course_id, pdf, admin)
    assert job["state"] == "done", job

    paper = client.post(
        f"/courses/{course_id}/pyq-papers/text", headers=admin,
        json={"year": 2024, "label": "End Semester 2024",
              "text": "Q1. Explain second normal form with an example.\n\n"
                      "Q2. Define a transitive dependency and give one instance."},
    ).json()

    bp = client.post(f"/courses/{course_id}/blueprints", headers=admin).json()
    client.put(
        f"/blueprints/{bp['blueprint_id']}/constraints", headers=admin,
        json=[{"course_outcome": "CO1 Explain relational normalisation",
               "concept": "third normal form", "bloom_level": "understand",
               "marks": 5, "question_type": "short_answer"}],
    )
    frozen = client.post(f"/blueprints/{bp['blueprint_id']}/freeze", headers=admin).json()
    run = client.post(f"/blueprints/{frozen['blueprint_id']}/generate", headers=admin).json()
    candidate = client.get(f"/runs/{run['run_id']}/candidates", headers=admin).json()[0]
    client.post(f"/candidates/{candidate['question_id']}/review",
                json={"decision": "accept"}, headers=admin)
    entry = client.post("/candidates/publish", headers=admin,
                        json={"question_ids": [candidate["question_id"]]}).json()[0]
    monkey.undo()
    return {
        "course_id": course_id, "material_id": job["material_id"],
        "paper_id": paper["paper_id"], "blueprint_id": frozen["blueprint_id"],
        "run_id": run["run_id"], "question_id": candidate["question_id"],
        "entry_id": entry["entry_id"], "admin": admin,
    }


def test_an_outsider_cannot_read_another_subject(client, foreign):
    """One GET per router, each naming an object that exists."""
    outsider = login(client, "reviewer1@example.edu")
    for path in (
        f"/courses/{foreign['course_id']}/materials",
        f"/materials/{foreign['material_id']}/chunks",
        f"/courses/{foreign['course_id']}/pyq-papers",
        f"/pyq-papers/{foreign['paper_id']}/questions",
        f"/courses/{foreign['course_id']}/blueprints",
        f"/blueprints/{foreign['blueprint_id']}",
        f"/blueprints/{foreign['blueprint_id']}/runs",
        f"/runs/{foreign['run_id']}",
        f"/runs/{foreign['run_id']}/candidates",
        f"/runs/{foreign['run_id']}/reviews",
        f"/candidates/{foreign['question_id']}/evidence",
        f"/bank/entries/{foreign['entry_id']}",
        f"/courses/{foreign['course_id']}/novelty/pairs",
        f"/courses/{foreign['course_id']}/novelty/model",
        f"/courses/{foreign['course_id']}/ranker/model",
        f"/courses/{foreign['course_id']}/members",
        f"/evaluation/blueprints/{foreign['blueprint_id']}/study",
        f"/evaluation/blueprints/{foreign['blueprint_id']}/report",
        f"/evaluation/blueprints/{foreign['blueprint_id']}/queue",
    ):
        assert client.get(path, headers=outsider).status_code == 404, path


def test_an_outsider_cannot_change_another_subject(client, foreign, tmp_path):
    outsider = login(client, "reviewer1@example.edu")
    pdf = write_pdf(tmp_path / "x.pdf", PASSAGE)
    with pdf.open("rb") as f:
        assert client.post(f"/courses/{foreign['course_id']}/materials",
                           files={"files": ("x.pdf", f, "application/pdf")},
                           headers=outsider).status_code == 404
    assert client.post(f"/courses/{foreign['course_id']}/blueprints", headers=outsider).status_code == 404
    assert client.post(f"/blueprints/{foreign['blueprint_id']}/generate", headers=outsider).status_code == 404
    assert client.post(f"/candidates/{foreign['question_id']}/review",
                       json={"decision": "accept"}, headers=outsider).status_code == 404
    assert client.post("/candidates/publish", json={"question_ids": [foreign["question_id"]]},
                       headers=outsider).status_code == 404
    assert client.delete(f"/pyq-papers/{foreign['paper_id']}", headers=outsider).status_code == 404
    assert client.post(f"/courses/{foreign['course_id']}/novelty/train", headers=outsider).status_code == 404
    assert client.post(f"/courses/{foreign['course_id']}/members", headers=outsider,
                       json={"user_id": str(foreign["course_id"]), "role": "reviewer"}).status_code == 404


def test_a_listing_never_leaks_another_subject(client, foreign):
    """The unscoped listings are the easiest place to leak, because nothing in the URL says which
    subject is being asked about."""
    outsider = login(client, "reviewer1@example.edu")
    assert foreign["course_id"] not in [c["course_id"] for c in client.get("/courses", headers=outsider).json()]
    entries = client.get("/bank/entries", headers=outsider).json()
    assert foreign["entry_id"] not in [e["entry_id"] for e in entries]
    # and the administrator, who is in every subject, does see it
    assert foreign["entry_id"] in [
        e["entry_id"] for e in client.get("/bank/entries", headers=foreign["admin"]).json()
    ]


def test_a_coordinator_grants_only_on_their_own_subject(client, new_course):
    """The point of the table: standing on one subject confers none on another."""
    from app.db import SessionLocal
    from app.models import User

    admin = login(client, "coordinator@example.edu")
    mine = new_course("MINE", "Mine", members=False)
    theirs = new_course("THEIRS", "Theirs", members=False)
    with SessionLocal() as db:
        me = db.scalar(select(User).where(User.email == "reviewer1@example.edu"))
        other = db.scalar(select(User).where(User.email == "reviewer2@example.edu"))
        me_id, other_id = str(me.user_id), str(other.user_id)

    assert client.post(f"/courses/{mine}/members", json={"user_id": me_id, "role": "coordinator"},
                       headers=admin).status_code == 201
    coordinator = login(client, "reviewer1@example.edu")
    assert client.post(f"/courses/{mine}/members", json={"user_id": other_id, "role": "reviewer"},
                       headers=coordinator).status_code == 201
    assert client.post(f"/courses/{theirs}/members", json={"user_id": other_id, "role": "reviewer"},
                       headers=coordinator).status_code == 404
    # and a coordinator cannot strand the subject by removing themselves
    assert client.delete(f"/courses/{mine}/members/{me_id}", headers=coordinator).status_code == 400


def test_a_reviewer_cannot_do_a_coordinators_work_on_their_own_subject(client, foreign):
    """Scoping replaces the old platform-wide role check; it does not loosen it. A member of the
    subject with the wrong role on it gets 403, because the object is not hidden from them."""
    admin = login(client, "coordinator@example.edu")
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        reviewer = db.scalar(select(User).where(User.email == "reviewer2@example.edu"))
        reviewer_id = str(reviewer.user_id)
    client.post(f"/courses/{foreign['course_id']}/members",
                json={"user_id": reviewer_id, "role": "reviewer"}, headers=admin)

    headers = login(client, "reviewer2@example.edu")
    assert client.get(f"/courses/{foreign['course_id']}/blueprints", headers=headers).status_code == 200
    assert client.post(f"/courses/{foreign['course_id']}/blueprints", headers=headers).status_code == 403
    assert client.post(f"/courses/{foreign['course_id']}/novelty/train", headers=headers).status_code == 403


def test_platform_administration_is_not_a_subject_role(client, foreign):
    """Admitting somebody to the platform is an administrator's act, and a coordinator of a
    subject is not one."""
    admin = login(client, "coordinator@example.edu")
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        target = db.scalar(select(User).where(User.email == "reviewer2@example.edu"))
        target_id = str(target.user_id)
    client.post(f"/courses/{foreign['course_id']}/members",
                json={"user_id": target_id, "role": "coordinator"}, headers=admin)

    coordinator = login(client, "reviewer2@example.edu")
    assert client.get("/auth/people", headers=coordinator).status_code == 403
    assert client.post(f"/auth/people/{target_id}/role", json={"role": "admin"},
                       headers=coordinator).status_code == 403
    assert client.post("/courses", json={"code": "NEW", "title": "New"},
                       headers=coordinator).status_code == 403
    assert client.post("/courses", json={"code": "NEW", "title": "New"},
                       headers=admin).status_code == 201
