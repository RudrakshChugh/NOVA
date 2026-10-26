"""Novelty engine end to end (plan §3.1, §11 steps 1-2): ingest a past-year paper, construct and
split the labelled pair set, fit the combiner, and catch a duplicate inside a generation run.

Runs on its own course so no other module's corpus or blueprint can change the outcome.
"""

import pytest

from app.llm.base import GenerationResult
from app.services import orchestrator
from app.services import novelty as novelty_engine
from app.services.novelty import dataset
from app.services.novelty.combiner import NoveltyCombiner

from .conftest import first_passage_sentence, login, write_pdf

MATERIAL = (
    "A data flow diagram shows how data moves through a system. "
    "A data store is drawn as two horizontal parallel lines and depicts data at rest. "
    "A process is drawn as a circle and depicts work performed on data. "
    "A source or sink is drawn as a square and depicts the origin or destination of data. "
    "Balancing requires that the inputs to and outputs from a process are conserved when it is "
    "decomposed into a lower level diagram. "
    "A context diagram represents the whole system as a single process."
)

PAST_PAPER = """1. Define a data store and state how it is drawn in a data flow diagram. [2]

2. Explain how balancing conserves inputs and outputs when a process is decomposed. [5]

3. Describe the difference between a context diagram and a level-1 diagram. [5]

4. Calculate the number of data flows crossing the boundary when 4 external entities each send 3
   flows into the system. [5]

5. List the four symbols used in a data flow diagram and state what each one depicts. [4]

6. Explain why a process cannot have only outputs in a data flow diagram. [5]

7. Describe how functional decomposition breaks a single system process into sub-processes. [5]

8. Define a source and a sink, and state how each is drawn. [3]

9. Compare a composite data flow with the component flows it splits into at the next level. [6]

10. Calculate how many levels of decomposition are needed for 27 primitive processes when each
    process is split into 3 at every level. [4]

11. Explain the rule that data cannot move directly from one data store to another. [5]

12. List the steps of process modelling and state the deliverable produced at each step. [6]
"""

# Question 2 of the past paper, which the first draft below reproduces almost verbatim.
DUPLICATED = "Explain how balancing conserves inputs and outputs when a process is decomposed."
ORIGINAL = "Explain why a context diagram represents the whole system as a single process."


class DuplicateThenNovelAdapter:
    """First draft repeats a past-year question; the redraft asks something else."""

    backend_id = "stub:novelty"

    def __init__(self) -> None:
        self.calls = 0
        self.saw_novelty_instruction = False

    def generate(self, req):
        self.calls += 1
        if "already been set in a past paper" in req.user:
            self.saw_novelty_instruction = True
            stem, answer = ORIGINAL, (
                "A context diagram represents the whole system as a single process so that the "
                "system boundary and its external entities are visible before any decomposition."
            )
        else:
            stem, answer = DUPLICATED, (
                "Balancing requires that the inputs to and outputs from a process are conserved "
                "when it is decomposed into a lower level diagram, so no flow appears or vanishes."
            )
        return GenerationResult(
            data={
                "stem": stem,
                "answer_key": answer,
                "options": [],
                "citations": [{"passage": 1, "quote": first_passage_sentence(req.user)}],
            },
            model_version="stub",
            temperature=req.temperature,
            seed=req.seed,
        )


@pytest.fixture(scope="module")
def course(client, new_course, tmp_path_factory):
    """A course with indexed material and a frozen single-row blueprint."""
    coordinator = login(client, "coordinator@example.edu")
    course_id = new_course("NOV101", "Novelty Engine Course")

    pdf = write_pdf(tmp_path_factory.mktemp("nov") / "unit.pdf", MATERIAL)
    with pdf.open("rb") as f:
        r = client.post(f"/courses/{course_id}/materials", files={"file": ("unit.pdf", f, "application/pdf")},
                        headers=coordinator)
    assert r.status_code == 201, r.text

    bp = client.post(f"/courses/{course_id}/blueprints", headers=coordinator).json()
    rows = [{
        "course_outcome": "CO1 Explain data flow diagram notation and decomposition",
        "concept": "balancing and context diagrams",
        "bloom_level": "understand", "marks": 5,
        "question_type": "short_answer", "target_difficulty": "easy",
    }]
    client.put(f"/blueprints/{bp['blueprint_id']}/constraints", json=rows, headers=coordinator)
    frozen = client.post(f"/blueprints/{bp['blueprint_id']}/freeze", headers=coordinator).json()
    return {"course_id": course_id, "blueprint_id": frozen["blueprint_id"], "coordinator": coordinator}


def test_pyq_paper_is_split_into_questions_with_metadata(client, course):
    body = {"year": 2024, "label": "End Semester 2024", "text": PAST_PAPER}
    r = client.post(f"/courses/{course['course_id']}/pyq-papers/text", json=body, headers=course["coordinator"])
    assert r.status_code == 201, r.text
    assert r.json()["question_count"] == 12

    questions = client.get(f"/pyq-papers/{r.json()['paper_id']}/questions").json()
    by_number = {q["number"]: q for q in questions}
    assert set(by_number) == {str(i) for i in range(1, 13)}

    # marks are parsed off the end of the stem, not left in the question text
    assert by_number["1"]["marks"] == 2 and by_number["2"]["marks"] == 5
    assert "[2]" not in by_number["1"]["text"]
    # type and Bloom level are inferred for the structural signal to compare
    assert by_number["4"]["question_type"] == "numerical"
    assert by_number["1"]["bloom_level"] == "remember"
    assert by_number["2"]["bloom_level"] == "understand"


def test_pyq_corpus_is_listed_for_the_course(client, course):
    papers = client.get(f"/courses/{course['course_id']}/pyq-papers").json()
    assert len(papers) == 1 and papers[0]["year"] == 2024 and papers[0]["question_count"] == 12


def test_pair_set_is_built_stratified_and_split(client, course):
    body = {"positives_per_question": 2, "hard_negatives_per_question": 1, "easy_negatives_per_question": 1}
    r = client.post(f"/courses/{course['course_id']}/novelty/pairs", json=body, headers=course["coordinator"])
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["added"] > 0 and result["total"] == result["added"]

    both_labels, both_strata = set(), set()
    for rows in result["composition"].values():
        both_labels.update(k for k in ("duplicate", "non_duplicate") if rows[k])
        both_strata.update(k for k in ("surface", "hard") if rows[k])
    assert both_labels == {"duplicate", "non_duplicate"}
    assert "hard" in both_strata  # the paraphrase/renumber cases plan §5 requires

    # rebuilding is idempotent: no pair is duplicated and no fold is reassigned
    before = client.get("/novelty/pairs").json()
    again = client.post(f"/courses/{course['course_id']}/novelty/pairs", json=body,
                        headers=course["coordinator"]).json()
    assert again["added"] == 0
    assert client.get("/novelty/pairs").json()["composition"] == before["composition"]


def test_split_manifest_records_the_seed_and_every_fold(client, course, tmp_path):
    from app.services.novelty.dataset import export_split_manifest
    from app.db import SessionLocal
    import json

    path = tmp_path / "novelty_split.json"
    with SessionLocal() as db:
        result = export_split_manifest(db, path)
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["seed"] == dataset.SPLIT_SEED
    assert manifest["fractions"] == {"train": 0.6, "val": 0.2, "test": 0.2}
    assert len(manifest["pairs"]) == result["pairs"]
    assert set(manifest["pairs"].values()) <= {"train", "val", "test"}


def test_signals_separate_duplicates_from_hard_negatives(client, course):
    """The engine's premise: a single embedding threshold is not enough, but the four signals
    together move duplicates above non-duplicates."""
    from app.db import SessionLocal

    with SessionLocal() as db:
        samples = (
            dataset.samples(db, course["course_id"], "train")
            + dataset.samples(db, course["course_id"], "val")
            + dataset.samples(db, course["course_id"], "test")
        )
    assert samples
    duplicates = [s for s, label in samples if label]
    non_duplicates = [s for s, label in samples if not label]
    assert duplicates and non_duplicates

    def mean(rows, name):
        return sum(r[name] for r in rows) / len(rows)

    for name in ("semantic", "lexical", "structural", "numerical"):
        assert mean(duplicates, name) > mean(non_duplicates, name), name
        assert all(0.0 <= r[name] <= 1.0 for r in samples and duplicates + non_duplicates), name


def test_combiner_trains_on_train_and_val_only_and_reports_weights(client, course):
    r = client.post(f"/courses/{course['course_id']}/novelty/train", headers=course["coordinator"])
    assert r.status_code == 200, r.text
    card = r.json()
    if not card.get("trained"):
        pytest.skip(f"not enough labelled pairs in this corpus: {card.get('reason')}")

    assert card["is_fitted"] and card["algorithm"] == "logistic_regression"
    assert set(card["coefficients"]) == {"semantic", "lexical", "structural", "numerical"}
    assert card["params"]["selected_on"] == "validation fold"
    assert card["test_evaluations"] == []  # training never touches the test fold
    assert novelty_engine.active_combiner_path().exists()

    live = client.get("/novelty/model").json()
    assert live["version"] == card["version"] and live["threshold"] == card["threshold"]


def test_test_fold_evaluation_is_separate_and_counts_its_accesses(client, course):
    if not novelty_engine.active_combiner_path().exists():
        pytest.skip("combiner not fitted")
    r = client.post(f"/courses/{course['course_id']}/novelty/evaluate-test", headers=course["coordinator"])
    assert r.status_code == 200, r.text
    result = r.json()
    if not result.get("evaluated"):
        pytest.skip(result.get("reason", ""))

    assert result["test_fold_accesses"] == 1 and result["protocol_note"] == "first access"
    for key in ("precision", "recall", "f1"):
        interval = result["model"][key]
        assert interval["low"] <= interval["point"] <= interval["high"]
    assert "single_signal_baseline" in result  # the one-threshold comparison plan §3.1 demands

    second = client.post(f"/courses/{course['course_id']}/novelty/evaluate-test",
                         headers=course["coordinator"]).json()
    assert second["test_fold_accesses"] == 2
    assert "single reported evaluation" in second["protocol_note"]


@pytest.fixture
def untrained_combiner(monkeypatch):
    """Pin the documented fallback — equal-weight mean, threshold 0.85 — so these tests assert the
    redraft mechanism rather than the fit of a combiner trained on a twelve-question corpus."""
    monkeypatch.setattr(novelty_engine, "load_combiner", NoveltyCombiner.untrained)


def test_generation_catches_a_duplicate_and_redrafts(client, course, monkeypatch, untrained_combiner):
    adapter = DuplicateThenNovelAdapter()
    monkeypatch.setattr(orchestrator, "get_adapter", lambda: adapter)
    reviewer = login(client, "reviewer1@example.edu")

    run = client.post(f"/blueprints/{course['blueprint_id']}/generate", headers=reviewer).json()
    run = client.get(f"/runs/{run['run_id']}").json()
    assert run["status"] == "completed", run["error"]

    candidate = client.get(f"/runs/{run['run_id']}/candidates").json()[0]
    novelty = candidate["novelty"]
    assert novelty is not None

    # the first draft repeated a past-year question, so the model was sent back with it named
    assert adapter.saw_novelty_instruction
    assert adapter.calls == 2
    assert candidate["stem"] == ORIGINAL
    assert candidate["state"] == "novel" and not novelty["is_duplicate"]

    # the report carries the per-signal breakdown and what it was compared against
    assert {s["name"] for s in novelty["signals"]} == {"semantic", "lexical", "structural", "numerical"}
    assert novelty["nearest_pyq"] is not None
    assert novelty["nearest_paper"] == "End Semester 2024 (2024)"
    assert 0.0 <= novelty["duplicate_probability"] <= 1.0


def test_duplicate_survives_when_the_redraft_budget_is_spent(client, course, monkeypatch, untrained_combiner):
    """With no redraft budget the duplicate is shown to the reviewer flagged, not silently kept."""
    from app.config import settings

    class AlwaysDuplicate(DuplicateThenNovelAdapter):
        def generate(self, req):
            self.calls += 1
            return GenerationResult(
                data={
                    "stem": DUPLICATED,
                    "answer_key": (
                        "Balancing requires that the inputs to and outputs from a process are "
                        "conserved when it is decomposed into a lower level diagram."
                    ),
                    "options": [],
                    "citations": [{"passage": 1, "quote": first_passage_sentence(req.user)}],
                },
                model_version="stub", temperature=req.temperature, seed=req.seed,
            )

    adapter = AlwaysDuplicate()
    monkeypatch.setattr(orchestrator, "get_adapter", lambda: adapter)
    monkeypatch.setattr(settings, "novelty_redraft_budget", 0)
    reviewer = login(client, "reviewer1@example.edu")

    run = client.post(f"/blueprints/{course['blueprint_id']}/generate", headers=reviewer).json()
    run = client.get(f"/runs/{run['run_id']}").json()
    assert run["status"] == "completed", run["error"]

    candidate = client.get(f"/runs/{run['run_id']}/candidates").json()[0]
    assert adapter.calls == 1
    assert candidate["state"] == "duplicate"
    assert candidate["novelty"]["is_duplicate"]
    assert candidate["novelty"]["nearest_pyq"]["text"].startswith("Explain how balancing conserves")
