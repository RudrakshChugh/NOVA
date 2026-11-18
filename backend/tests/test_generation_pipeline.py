"""Generation against a real database: the behaviour that only exists once rows are written.

  * a draft that repeats another question of its own run, or one already in the bank, is caught
    and rewritten, and the report says what it repeated;
  * past-year questions are shown to the model before it drafts;
  * a reply that cannot be read is retried, and a row that never can be is skipped, not fatal;
  * a run or an upload orphaned by a restart is failed rather than left "in progress" forever;
  * the readiness report says what a run started now would trip over.
"""

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.llm import GenerationResult
from app.models import CandidateQuestion, GenerationRun, IngestionJob
from app.routers import materials
from app.services import orchestrator

from .conftest import first_passage_sentence, login, upload_material, write_pdf

MATERIAL = (
    "A data flow diagram models how data moves through a system. It uses four symbols: process, "
    "data store, external entity and data flow. A context diagram shows the whole system as a "
    "single process connected to its external entities. A level-1 diagram decomposes that "
    "process into sub-processes and data stores while keeping the external flows balanced. "
    "Balancing means every flow that enters or leaves the parent process appears on the child."
)
FIRST = "Explain how a context diagram represents the external entities of a data flow diagram."
SECOND = "Describe why balancing is required when a data flow diagram is decomposed to level 1."
ANSWER = (
    "A data flow diagram models how data moves through a system using processes, data stores, "
    "external entities and flows. The context diagram shows the whole system as one process, "
    "and decomposition keeps every external flow balanced between parent and child diagrams."
)
ROW = {"course_outcome": "CO2 Explain data flow diagram notation", "concept": "data flow diagram",
       "bloom_level": "understand", "marks": 5, "question_type": "short_answer"}


class Stub:
    """Writes FIRST until it is told the draft repeats something, then SECOND. Records every
    request so a test can read what the model was actually sent."""

    backend_id = "stub:pipeline"

    def __init__(self, reply=None):
        self.requests = []
        self.reply = reply

    def generate(self, req):
        self.requests.append(req)
        if self.reply is not None:
            data = self.reply(req, len(self.requests))
            if data is not None:
                return GenerationResult(data=data, model_version="stub", temperature=req.temperature, seed=req.seed)
        stem = SECOND if "previous draft repeats" in req.user else FIRST
        return GenerationResult(
            data={"stem": stem, "answer_key": ANSWER, "options": [],
                  "citations": [{"passage": 1, "quote": first_passage_sentence(req.user)}]},
            model_version="stub", temperature=req.temperature, seed=req.seed,
        )


@pytest.fixture
def subject(client, new_course, tmp_path, request):
    """A subject of its own with indexed material and a helper that freezes a blueprint."""
    coordinator = login(client, "coordinator@example.edu")
    course_id = new_course(f"GP{abs(hash(request.node.name)) % 10**8}", "Generation Pipeline")
    pdf = write_pdf(tmp_path / "dfd.pdf", MATERIAL)
    assert upload_material(client, course_id, pdf, coordinator)["state"] == "done"

    def freeze(rows):
        bp = client.post(f"/courses/{course_id}/blueprints", headers=coordinator).json()
        client.put(f"/blueprints/{bp['blueprint_id']}/constraints", json=rows, headers=coordinator)
        frozen = client.post(f"/blueprints/{bp['blueprint_id']}/freeze", headers=coordinator)
        assert frozen.status_code == 200, frozen.text
        return frozen.json()["blueprint_id"]

    return {"course_id": course_id, "headers": coordinator, "freeze": freeze}


def _generate(client, subject, blueprint_id, adapter, monkeypatch):
    monkeypatch.setattr(orchestrator, "get_adapter", lambda: adapter)
    started = client.post(f"/blueprints/{blueprint_id}/generate", headers=subject["headers"])
    assert started.status_code == 202, started.text
    run = client.get(f"/runs/{started.json()['run_id']}", headers=subject["headers"]).json()
    candidates = client.get(f"/runs/{run['run_id']}/candidates", headers=subject["headers"]).json()
    return run, sorted(candidates, key=lambda c: c["constraint"]["position"])


# --- novelty beyond past papers ----------------------------------------------------------------


def test_two_identical_rows_do_not_get_the_same_question(client, subject, monkeypatch):
    adapter = Stub()
    run, (q1, q2) = _generate(client, subject, subject["freeze"]([ROW, ROW]), adapter, monkeypatch)

    assert run["status"] == "completed" and run["error"] is None
    assert q1["stem"] == FIRST and q1["state"] == "novel"
    # the second row first repeated the first, was told so, and was rewritten
    assert q2["stem"] == SECOND and q2["state"] == "novel" and q2["repair_attempts"] == 1
    redraft = adapter.requests[-1].user
    assert "repeats a question that is already on this paper" in redraft and FIRST in redraft

    novelty = q2["novelty"]
    assert novelty["nearest_source"] == "run" and novelty["nearest_label"] == "Q1 of this run"
    assert novelty["nearest_text"] == FIRST and novelty["nearest_pyq"] is None
    assert not novelty["is_duplicate"] and novelty["duplicate_probability"] < novelty["threshold"]
    assert {s["name"] for s in novelty["signals"]} == {"semantic", "lexical", "structural", "numerical"}


def test_the_second_row_is_told_about_the_first_before_it_drafts(client, subject, monkeypatch):
    adapter = Stub()
    _generate(client, subject, subject["freeze"]([ROW, ROW]), adapter, monkeypatch)
    first_prompt, second_prompt = adapter.requests[0].user, adapter.requests[1].user
    assert "already on this paper" not in first_prompt
    assert "These questions are already on this paper." in second_prompt
    assert f"do not reuse their scenario or setting:\n- {FIRST}" in second_prompt


def test_a_repeat_within_the_run_is_kept_and_named_when_the_budget_is_spent(client, subject, monkeypatch):
    monkeypatch.setattr(settings, "novelty_redraft_budget", 0)
    _, (q1, q2) = _generate(client, subject, subject["freeze"]([ROW, ROW]), Stub(), monkeypatch)
    assert q1["state"] == "novel"
    assert q2["state"] == "duplicate" and q2["stem"] == FIRST
    assert q2["novelty"]["is_duplicate"] and q2["novelty"]["nearest_source"] == "run"
    assert q2["novelty"]["duplicate_probability"] == pytest.approx(1.0)


def test_a_question_already_in_the_bank_is_not_generated_again(client, subject, monkeypatch):
    blueprint_id = subject["freeze"]([ROW])
    _, (first,) = _generate(client, subject, blueprint_id, Stub(), monkeypatch)
    assert first["stem"] == FIRST and first["novelty"] is None  # nothing existed to compare with
    h = subject["headers"]
    client.post(f"/candidates/{first['question_id']}/review", json={"decision": "accept"}, headers=h)
    published = client.post("/candidates/publish", json={"question_ids": [first["question_id"]]}, headers=h)
    assert published.status_code in (200, 201), published.text

    adapter = Stub()
    _, (again,) = _generate(client, subject, blueprint_id, adapter, monkeypatch)
    assert again["stem"] == SECOND and again["state"] == "novel"
    assert again["novelty"]["nearest_source"] == "bank" and again["novelty"]["nearest_text"] == FIRST
    assert "is already in the question bank" in adapter.requests[-1].user


def test_the_internal_check_can_be_switched_off(client, subject, monkeypatch):
    monkeypatch.setattr(settings, "novelty_internal_check", False)
    _, (q1, q2) = _generate(client, subject, subject["freeze"]([ROW, ROW]), Stub(), monkeypatch)
    assert q1["stem"] == q2["stem"] == FIRST and q2["state"] == "novel" and q2["novelty"] is None


def test_past_questions_on_the_topic_are_shown_before_the_first_draft(client, subject, monkeypatch):
    h = subject["headers"]
    # no full stop: the paper splitter trims trailing punctuation from each question it stores
    past = "Draw a context diagram and a level-1 data flow diagram for a library system"
    r = client.post(f"/courses/{subject['course_id']}/pyq-papers/text", headers=h, json={
        "year": 2024, "label": "End Semester 2024",
        "text": f"Q1. {past}.\n\nQ2. State the law of demand and name two exceptions to it.",
    })
    assert r.status_code in (200, 201), r.text

    adapter = Stub()
    _, (q,) = _generate(client, subject, subject["freeze"]([ROW]), adapter, monkeypatch)
    prompt = adapter.requests[0].user
    assert "have appeared in earlier papers" in prompt and past in prompt
    assert "law of demand" not in prompt, "an unrelated past question is noise, not guidance"
    assert q["novelty"]["nearest_source"] == "pyq" and q["novelty"]["nearest_pyq"]["text"].startswith("Draw a context")


# --- seeds -------------------------------------------------------------------------------------


def test_each_row_is_drafted_with_its_own_seed_and_the_run_keeps_its_own(client, subject, monkeypatch):
    monkeypatch.setattr(settings, "novelty_internal_check", False)
    adapter = Stub()
    run, _ = _generate(client, subject, subject["freeze"]([ROW, ROW, ROW]), adapter, monkeypatch)
    seeds = [r.seed for r in adapter.requests]
    assert seeds == [run["seed"], run["seed"] + 1, run["seed"] + 2]


# --- replies that are not questions ------------------------------------------------------------


def test_a_garbled_reply_is_asked_for_again_and_costs_nothing(client, subject, monkeypatch):
    adapter = Stub(reply=lambda req, n: {"stem": "", "answer_key": ""} if n == 1 else None)
    run, (q,) = _generate(client, subject, subject["freeze"]([ROW]), adapter, monkeypatch)
    assert run["status"] == "completed" and run["error"] is None
    assert q["stem"] == FIRST and q["repair_attempts"] == 0 and len(adapter.requests) == 2


def test_a_row_that_cannot_be_drafted_is_skipped_and_the_rest_still_run(client, subject, monkeypatch):
    other = {**ROW, "concept": "balancing", "marks": 4}

    def reply(req, n):
        return "not an object" if "- concept: balancing" in req.user else None

    run, candidates = _generate(client, subject, subject["freeze"]([ROW, other, ROW]), Stub(reply), monkeypatch)
    assert run["status"] == "completed"
    assert "1 of 3 row(s) could not be drafted" in run["error"] and "Q2:" in run["error"]
    assert [c["constraint"]["position"] for c in candidates] == [0, 2]
    assert all(c["stem"] for c in candidates), "no empty candidate may reach the review queue"
    assert run["candidate_count"] == 2


def test_a_run_where_nothing_can_be_drafted_fails_and_leaves_no_candidates(client, subject, monkeypatch):
    run, candidates = _generate(client, subject, subject["freeze"]([ROW]), Stub(lambda req, n: {}), monkeypatch)
    assert run["status"] == "failed" and run["error"].startswith("No row could be drafted")
    assert candidates == []


def test_a_backend_that_goes_away_fails_the_run_without_leaving_a_blank(client, subject, monkeypatch):
    def reply(req, n):
        if n == 2:
            raise RuntimeError("Ollama unreachable at http://localhost:11434")
        return None

    monkeypatch.setattr(settings, "novelty_internal_check", False)
    run, candidates = _generate(client, subject, subject["freeze"]([ROW, ROW]), Stub(reply), monkeypatch)
    assert run["status"] == "failed" and "Ollama unreachable" in run["error"]
    assert [c["stem"] for c in candidates] == [FIRST]


# --- restarts ----------------------------------------------------------------------------------


def test_a_run_orphaned_by_a_restart_is_failed_and_a_new_one_can_start(client, subject, monkeypatch):
    blueprint_id = subject["freeze"]([ROW])
    h = subject["headers"]
    with SessionLocal() as db:
        run = GenerationRun(blueprint_id=blueprint_id, backend_id="stub:pipeline", prompt_hash="x" * 64, status="running")
        db.add(run)
        db.flush()
        constraint_id = client.get(f"/blueprints/{blueprint_id}", headers=h).json()["constraints"][0]["constraint_id"]
        db.add(CandidateQuestion(run_id=run.run_id, constraint_id=constraint_id, stem="", answer_key="", state="drafted"))
        db.commit()
        run_id = run.run_id

    monkeypatch.setattr(orchestrator, "get_adapter", lambda: Stub())
    refused = client.post(f"/blueprints/{blueprint_id}/generate", headers=h)
    assert refused.status_code == 409 and "already in progress" in refused.json()["detail"]

    assert orchestrator.recover_interrupted_runs() >= 1
    stale = client.get(f"/runs/{run_id}", headers=h).json()
    assert stale["status"] == "failed" and stale["error"].startswith("Interrupted")
    assert stale["candidate_count"] == 0, "the blank candidate must go"
    assert client.post(f"/blueprints/{blueprint_id}/generate", headers=h).status_code == 202


def test_an_upload_orphaned_by_a_restart_is_failed(client, subject):
    with SessionLocal() as db:
        job = IngestionJob(course_id=subject["course_id"], filename="lost.pdf", state="queued")
        db.add(job)
        db.commit()
        job_id = job.job_id
    assert materials.recover_interrupted_jobs() >= 1
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        assert job.state == "failed" and "Interrupted" in job.detail and job.finished_at is not None
    # material that finished indexing before the restart is untouched
    listed = client.get(f"/courses/{subject['course_id']}/materials", headers=subject["headers"]).json()
    assert len(listed) == 1 and listed[0]["chunk_count"] >= 1


# --- readiness ---------------------------------------------------------------------------------


def test_the_readiness_report_names_what_a_run_would_trip_over(client, subject, monkeypatch):
    monkeypatch.setattr(settings, "llm_backend", "ollama")
    monkeypatch.setattr(settings, "ollama_url", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setattr(settings, "failover_probe_timeout", 0.5)
    r = client.get(f"/courses/{subject['course_id']}/status", headers=subject["headers"])
    assert r.status_code == 200, r.text
    checks = {c["key"]: c for c in r.json()["checks"]}
    assert set(checks) == {"database", "embeddings", "llm", "material", "outcomes", "novelty"}
    assert checks["database"]["state"] == checks["embeddings"]["state"] == checks["material"]["state"] == "ok"
    assert checks["llm"]["state"] == "bad" and "not reachable" in checks["llm"]["detail"]
    assert checks["outcomes"]["state"] == "warn" and checks["novelty"]["state"] == "warn"
    assert r.json()["ready"] is False


def test_readiness_is_scoped_to_the_subject_and_health_is_open(client, new_course):
    outsider_course = new_course("GPCLOSED", "Not Yours", members=False)
    reviewer = login(client, "reviewer1@example.edu")
    assert client.get(f"/courses/{outsider_course}/status", headers=reviewer).status_code in (403, 404)
    assert client.get("/health", headers={"Authorization": ""}).json() == {"status": "ok"}


# --- material that does not cover the row ------------------------------------------------------


def test_a_row_the_material_does_not_cover_is_warned_about_and_never_drafted(client, subject, monkeypatch):
    """The anti-hallucination rule: no passage, no question."""
    off_topic = {"course_outcome": "CO5 Explain monetary policy", "concept": "central bank interest rates",
                 "bloom_level": "understand", "marks": 5, "question_type": "short_answer"}
    h = subject["headers"]
    bp = client.post(f"/courses/{subject['course_id']}/blueprints", headers=h).json()
    client.put(f"/blueprints/{bp['blueprint_id']}/constraints", json=[ROW, off_topic], headers=h)
    validation = client.post(f"/blueprints/{bp['blueprint_id']}/validate", headers=h).json()
    assert validation["ok"], "coverage is advisory and must not block freezing"
    assert len(validation["warnings"]) == 1
    assert "Row 2" in validation["warnings"][0] and "central bank interest rates" in validation["warnings"][0]
    blueprint_id = client.post(f"/blueprints/{bp['blueprint_id']}/freeze", headers=h).json()["blueprint_id"]

    adapter = Stub()
    run, candidates = _generate(client, subject, blueprint_id, adapter, monkeypatch)
    assert run["status"] == "completed" and "Q2:" in run["error"] and "nothing close enough" in run["error"]
    assert [c["constraint"]["position"] for c in candidates] == [0]
    assert all("central bank" not in r.user for r in adapter.requests), "the model must never be asked"


def test_the_coverage_floor_can_be_switched_off(client, subject, monkeypatch):
    monkeypatch.setattr(settings, "retrieval_min_similarity", 0.0)
    off_topic = {**ROW, "concept": "central bank interest rates", "course_outcome": "CO5 Explain monetary policy"}
    run, candidates = _generate(client, subject, subject["freeze"]([off_topic]), Stub(), monkeypatch)
    assert run["error"] is None and len(candidates) == 1


# --- twin rows and the evidence check, against the database --------------------------------------


def test_twin_rows_are_given_different_task_forms_and_different_passages(client, subject, tmp_path, monkeypatch):
    # enough material for two different shares of passages
    extra = " ".join(
        f"Rule {i} of data flow diagram drawing states that every process number {i} must have an input flow and an output flow."
        for i in range(1, 40)
    )
    pdf = write_pdf(tmp_path / "more.pdf", extra)
    assert upload_material(client, subject["course_id"], pdf, subject["headers"])["state"] == "done"
    monkeypatch.setattr(settings, "novelty_internal_check", False)

    adapter = Stub()
    _generate(client, subject, subject["freeze"]([ROW, ROW]), adapter, monkeypatch)
    first, second = adapter.requests[0].user, adapter.requests[1].user
    forms = orchestrator.TASK_FORMS[ROW["bloom_level"]]
    assert f"Task form for this row: {forms[0]}." in first and f"Task form for this row: {forms[1]}." in second

    def passages(prompt):
        return prompt.split("Course passages:\n", 1)[1].split("\n\nReturn the question", 1)[0].split("\n\n")

    a, b = passages(first), passages(second)
    assert a and b and [p.split(") ", 1)[1] for p in a] != [p.split(") ", 1)[1] for p in b]


def test_a_blueprint_without_twins_is_drafted_exactly_as_before(client, subject, monkeypatch):
    adapter = Stub()
    _generate(client, subject, subject["freeze"]([ROW, {**ROW, "concept": "context diagram"}]), adapter, monkeypatch)
    assert all("Task form for this row" not in r.user for r in adapter.requests)


def test_the_evidence_report_is_stored_with_the_candidate_and_only_verified_quotes_count(client, subject, monkeypatch):
    monkeypatch.setattr(settings, "claim_check_enabled", True)
    real = "A data flow diagram models how data moves through a system."

    def reply(req, n):
        if "You check an examination answer key" not in req.system:
            return None
        return {"claims": [
            {"text": "A DFD models data movement.", "kind": "material", "passage": 1, "quote": real},
            {"text": "A DFD needs seven processes.", "kind": "material", "passage": 1, "quote": "A data flow diagram needs seven processes."},
            {"text": "The library is the system.", "kind": "scenario", "passage": 0, "quote": ""},
        ]}

    adapter = Stub(reply)
    run, (q,) = _generate(client, subject, subject["freeze"]([ROW]), adapter, monkeypatch)
    assert run["status"] == "completed" and q["stem"] == FIRST
    report = q["claim_check"]
    assert (report["material_total"], report["material_verified"], report["scenario_total"]) == (2, 1, 1)
    assert report["claims"][0]["verified"] and report["claims"][0]["quote"] == real
    assert not report["claims"][1]["verified"] and report["checked_by"] == "stub:pipeline"
    # one draft and its check; the unsupported statement sends it back once; then a second check
    assert len(adapter.requests) == 4 and q["repair_attempts"] == 1
    redraft = adapter.requests[2].user
    assert "states things about the subject that the passages do not say" in redraft
    assert "- A DFD needs seven processes." in redraft and "A DFD models data movement" not in redraft


def test_an_answer_fully_backed_by_the_material_is_not_rewritten(client, subject, monkeypatch):
    monkeypatch.setattr(settings, "claim_check_enabled", True)
    real = "A data flow diagram models how data moves through a system."

    def reply(req, n):
        if "You check an examination answer key" not in req.system:
            return None
        return {"claims": [{"text": "A DFD models data movement.", "kind": "material", "passage": 1, "quote": real}]}

    adapter = Stub(reply)
    _, (q,) = _generate(client, subject, subject["freeze"]([ROW]), adapter, monkeypatch)
    assert len(adapter.requests) == 2 and q["repair_attempts"] == 0
    assert q["claim_check"]["material_verified"] == q["claim_check"]["material_total"] == 1


def test_the_rewrite_for_evidence_happens_at_most_once(client, subject, monkeypatch):
    monkeypatch.setattr(settings, "claim_check_enabled", True)
    monkeypatch.setattr(settings, "evidence_redraft_budget", 0)

    def reply(req, n):
        if "You check an examination answer key" not in req.system:
            return None
        return {"claims": [{"text": "A DFD needs seven processes.", "kind": "material", "passage": 0, "quote": ""}]}

    adapter = Stub(reply)
    _, (q,) = _generate(client, subject, subject["freeze"]([ROW]), adapter, monkeypatch)
    assert len(adapter.requests) == 2 and q["state"] == "novel"
    assert q["claim_check"]["material_verified"] == 0, "the report is kept even though nothing more is done"


def test_a_failed_evidence_check_costs_nothing(client, subject, monkeypatch):
    monkeypatch.setattr(settings, "claim_check_enabled", True)

    def reply(req, n):
        if "You check an examination answer key" in req.system:
            raise RuntimeError("rate limited")
        return None

    run, (q,) = _generate(client, subject, subject["freeze"]([ROW]), Stub(reply), monkeypatch)
    assert run["status"] == "completed" and run["error"] is None
    assert q["stem"] == FIRST and q["claim_check"] is None
