"""Ranker end to end (plan §11 step 3): three review sittings on one course, then fit, compare
against both floors, and serve candidates in the learned order.

Runs on its own course so no other module's review records reach the model.
"""

import pytest
from sqlalchemy import select

from app.llm.base import GenerationResult
from app.services.ranker import models as rm
from app.services.ranker.features import FEATURE_NAMES

from .conftest import first_passage_sentence, login, write_pdf

MATERIAL = (
    "Normalisation removes redundancy from a relational schema. "
    "First normal form requires every attribute to hold a single atomic value. "
    "Second normal form removes partial dependencies on part of a composite key. "
    "Third normal form removes transitive dependencies between non-key attributes. "
    "Boyce-Codd normal form requires every determinant to be a candidate key. "
    "Denormalisation reintroduces redundancy deliberately to reduce join cost. "
    "A functional dependency holds when one attribute set determines another."
)

ROWS = [
    ("CO1 Explain relational normalisation", "first normal form", "remember", 2, "short_answer", "easy"),
    ("CO1 Explain relational normalisation", "second normal form", "understand", 5, "short_answer", "easy"),
    ("CO1 Explain relational normalisation", "third normal form", "understand", 5, "short_answer", "easy"),
    ("CO2 Apply normalisation to a schema", "functional dependencies", "apply", 5, "short_answer", "medium"),
    ("CO2 Apply normalisation to a schema", "Boyce-Codd normal form", "analyze", 10, "long_answer", "medium"),
    ("CO2 Apply normalisation to a schema", "denormalisation trade-offs", "evaluate", 5, "short_answer", "hard"),
    ("CO1 Explain relational normalisation", "atomic values", "remember", 2, "short_answer", "easy"),
]

BLOOM_OPENER = {
    "remember": "List", "understand": "Explain", "apply": "Apply",
    "analyze": "Compare", "evaluate": "Evaluate", "create": "Design",
}


class VaryingQualityAdapter:
    """Every third draft opens at the wrong Bloom level, so the review records that follow carry
    signal rather than a single label."""

    backend_id = "stub:ranker"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, req):
        self.calls += 1
        lines = req.user.splitlines()
        level = next(ln.split(": ", 1)[1].split(" (")[0] for ln in lines if ln.startswith("- Bloom level: "))
        concept = next(ln.split(": ", 1)[1] for ln in lines if ln.startswith("- concept: "))
        opener = BLOOM_OPENER[level] if self.calls % 3 else "Design"
        answer = (
            f"Normalisation removes redundancy from a relational schema, and {concept} is the step "
            "that addresses one particular kind of redundancy. A schema that satisfies it avoids "
            "the update anomalies that follow from storing the same fact twice, which is why the "
            "normal forms are applied in order when a relational schema is designed."
        )
        return GenerationResult(
            data={
                "stem": f"{opener} how {concept} applies when a relational schema is normalised.",
                "answer_key": answer,
                "options": [],
                "citations": [{"passage": 1, "quote": first_passage_sentence(req.user)}],
            },
            model_version="stub", temperature=req.temperature, seed=req.seed,
        )


@pytest.fixture(scope="module")
def reviewed_course(client, new_course, tmp_path_factory, request):
    """Three completed runs with every candidate reviewed, i.e. three review sittings."""
    from app.services import orchestrator

    monkeypatch = pytest.MonkeyPatch()
    request.addfinalizer(monkeypatch.undo)
    adapter = VaryingQualityAdapter()
    monkeypatch.setattr(orchestrator, "get_adapter", lambda: adapter)

    coordinator = login(client, "coordinator@example.edu")
    reviewer = login(client, "reviewer1@example.edu")
    course_id = new_course("RANK101", "Ranker Course")

    pdf = write_pdf(tmp_path_factory.mktemp("rank") / "unit.pdf", MATERIAL)
    with pdf.open("rb") as f:
        assert client.post(
            f"/courses/{course_id}/materials",
            files={"file": ("unit.pdf", f, "application/pdf")}, headers=coordinator,
        ).status_code == 201

    bp = client.post(f"/courses/{course_id}/blueprints", headers=coordinator).json()
    rows = [
        {"course_outcome": o, "concept": c, "bloom_level": b, "marks": m,
         "question_type": t, "target_difficulty": d}
        for o, c, b, m, t, d in ROWS
    ]
    client.put(f"/blueprints/{bp['blueprint_id']}/constraints", json=rows, headers=coordinator)
    frozen = client.post(f"/blueprints/{bp['blueprint_id']}/freeze", headers=coordinator).json()

    runs = []
    for _ in range(3):
        run = client.post(f"/blueprints/{frozen['blueprint_id']}/generate", headers=reviewer).json()
        status = client.get(f"/runs/{run['run_id']}").json()
        assert status["status"] == "completed", status["error"]
        for candidate in client.get(f"/runs/{run['run_id']}/candidates").json():
            body = (
                {"decision": "accept"} if candidate["compliance"]["is_compliant"]
                else {"decision": "reject", "reason": "does not match the blueprint row"}
            )
            assert client.post(f"/candidates/{candidate['question_id']}/review",
                               json=body, headers=reviewer).status_code == 200
        runs.append(run["run_id"])

    return {"course_id": course_id, "runs": runs, "coordinator": coordinator, "reviewer": reviewer}


def test_examples_are_collected_with_sittings_and_labels(reviewed_course):
    from app.db import SessionLocal
    from app.services.ranker.training import collect

    with SessionLocal() as db:
        examples = collect(db, reviewed_course["course_id"])

    assert len(examples) == len(ROWS) * 3
    assert len({e.session for e in examples}) == 3  # one sitting per run
    assert {e.accepted for e in examples} == {True, False}  # both labels present
    for e in examples:
        assert set(e.features) == set(FEATURE_NAMES)


def test_ranker_trains_compares_both_floors_and_records_its_choice(client, reviewed_course):
    r = client.post(f"/courses/{reviewed_course['course_id']}/ranker/train",
                    headers=reviewed_course["coordinator"])
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["trained"] and card["is_fitted"]

    metrics = card["metrics"]
    assert metrics["n_sessions"] == 3 and metrics["n_examples"] == len(ROWS) * 3
    comparison = metrics["cross_validation"]
    assert rm.COMPLIANCE_ONLY in comparison  # the floor is always evaluated
    for entry in comparison.values():
        interval = entry["ndcg"]
        assert interval["low"] <= interval["point"] <= interval["high"]

    selection = metrics["selection"]
    assert selection["chosen"] == card["algorithm"]
    assert selection["lightgbm_beat_both_floors"] == (selection["chosen"] == rm.LIGHTGBM)
    assert metrics["last_session_ndcg"]  # the chronologically last sitting, reported alone

    assert client.get("/ranker/model").json()["algorithm"] == card["algorithm"]


def test_candidates_come_back_in_the_learned_order(client, reviewed_course):
    from app.db import SessionLocal
    from app.models import CandidateQuestion
    from app.services.ranker import load_active
    from app.services.ranker.features import extract

    scorer = load_active()
    assert scorer is not None

    returned = client.get(f"/runs/{reviewed_course['runs'][0]}/candidates").json()
    ids = [c["question_id"] for c in returned]
    with SessionLocal() as db:
        scores = {
            str(q.question_id): scorer.score(extract(q))
            for q in db.scalars(select(CandidateQuestion).where(CandidateQuestion.question_id.in_(ids))).all()
        }

    survived = [c for c in returned if c["state"] not in ("violating", "duplicate", "drafted")]
    assert survived
    ranked = [scores[c["question_id"]] for c in survived]
    assert ranked == sorted(ranked, reverse=True)


def test_training_refuses_when_there_are_too_few_sittings(client, new_course):
    coordinator = login(client, "coordinator@example.edu")
    empty = new_course("RANK102", "No Reviews Course")
    result = client.post(f"/courses/{empty}/ranker/train", headers=coordinator).json()
    assert not result["trained"]
    assert "review sittings" in result["reason"]
    assert result["examples"] == 0
