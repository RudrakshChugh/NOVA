"""Evaluation harness end to end (plan §3.3): arms run under shared seeds, reviewers see a
blinded queue, and every reported metric arrives with its interval.

The properties worth testing here are protocol properties — that an arm really does switch off
what it claims to, that a blinded response cannot identify the arm, and that a difference whose
interval spans zero is reported as no difference — so those are asserted directly.
"""

import time

import pytest

from app.llm.base import GenerationResult
from app.services.evaluation import arms as A
from app.services.evaluation import harness

from .conftest import first_passage_sentence, login, write_pdf

MATERIAL = (
    "A deadlock occurs when a set of processes each hold a resource and wait for another. "
    "Mutual exclusion means a resource cannot be shared between two processes at once. "
    "Hold and wait means a process keeps a resource while requesting another. "
    "No preemption means a resource is released only voluntarily by the process holding it. "
    "Circular wait means a closed chain of processes each waits for the next. "
    "Deadlock prevention removes one of the four necessary conditions. "
    "The banker's algorithm avoids deadlock by refusing unsafe allocations."
)

ROWS = [
    ("CO1 Explain the conditions for deadlock", "mutual exclusion", "remember", 2, "short_answer", "easy"),
    ("CO1 Explain the conditions for deadlock", "circular wait", "understand", 5, "short_answer", "easy"),
    ("CO2 Apply deadlock prevention", "deadlock prevention", "apply", 5, "short_answer", "medium"),
]


class RecordingAdapter:
    """Records the prompt of every draft so the tests can check what each arm actually told the
    model, and varies quality so reviews carry both labels."""

    backend_id = "stub:evaluation"

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    def generate(self, req):
        self.prompts.append((req.system, req.user))
        lines = req.user.splitlines()
        concept = next((ln.split(": ", 1)[1] for ln in lines if ln.startswith("- concept: ")), "the topic")
        grounded = "Course passages:" in req.user
        citations = [{"passage": 1, "quote": first_passage_sentence(req.user)}] if grounded else []
        answer = (
            f"A deadlock occurs when a set of processes each hold a resource and wait for another, "
            f"and {concept} is one of the conditions that makes this possible. Removing it is what "
            "deadlock prevention does, which is why the four conditions are stated together."
        )
        return GenerationResult(
            data={"stem": f"Explain how {concept} contributes to deadlock.", "answer_key": answer,
                  "options": [], "citations": citations},
            model_version="stub", temperature=req.temperature, seed=req.seed,
        )


@pytest.fixture(scope="module")
def study(client, new_course, tmp_path_factory, request):
    """A frozen blueprint with the full system and the RAG baseline run under two seeds each."""
    from app.services import orchestrator

    monkeypatch = pytest.MonkeyPatch()
    request.addfinalizer(monkeypatch.undo)
    adapter = RecordingAdapter()
    monkeypatch.setattr(orchestrator, "get_adapter", lambda: adapter)

    coordinator = login(client, "coordinator@example.edu")
    course_id = new_course("EVAL101", "Evaluation Course")

    pdf = write_pdf(tmp_path_factory.mktemp("eval") / "unit.pdf", MATERIAL)
    with pdf.open("rb") as f:
        assert client.post(f"/courses/{course_id}/materials",
                           files={"file": ("unit.pdf", f, "application/pdf")},
                           headers=coordinator).status_code == 201

    bp = client.post(f"/courses/{course_id}/blueprints", headers=coordinator).json()
    rows = [
        {"course_outcome": o, "concept": c, "bloom_level": b, "marks": m,
         "question_type": t, "target_difficulty": d}
        for o, c, b, m, t, d in ROWS
    ]
    client.put(f"/blueprints/{bp['blueprint_id']}/constraints", json=rows, headers=coordinator)
    frozen = client.post(f"/blueprints/{bp['blueprint_id']}/freeze", headers=coordinator).json()

    launched = client.post(
        f"/evaluation/blueprints/{frozen['blueprint_id']}/study",
        json={"arms": [A.FULL_SYSTEM, A.FIXED_PROMPT_RAG, A.PLAIN_LLM], "seeds": 2},
        headers=coordinator,
    )
    assert launched.status_code == 202, launched.text

    # The launch returns as soon as the runs are queued, so the fixture waits for them. Without
    # this the queue and the report are read while arms are still generating, and whichever arm
    # happens to be unfinished reports no reviews — a race, not a failure.
    status = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = client.get(f"/evaluation/blueprints/{frozen['blueprint_id']}/study",
                            headers=coordinator).json()
        if status["complete"]:
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"study did not finish in time: {status}")

    return {
        "course_id": course_id, "blueprint_id": frozen["blueprint_id"],
        "coordinator": coordinator, "adapter": adapter, "launched": launched.json(),
    }


def test_every_arm_is_pinned_and_described(client):
    listing = client.get("/evaluation/arms").json()
    assert set(listing) == set(A.ARMS)
    assert listing[A.FULL_SYSTEM]["enforce_compliance"] and listing[A.FULL_SYSTEM]["rank"]
    assert not listing[A.PLAIN_LLM]["retrieval"] and not listing[A.PLAIN_LLM]["blueprint_in_prompt"]
    assert not listing[A.ABLATION_NO_BLUEPRINT]["enforce_compliance"]
    assert not listing[A.ABLATION_NO_NOVELTY]["enforce_novelty"]
    assert not listing[A.ABLATION_NO_RANKER]["rank"]
    for arm in listing.values():
        assert arm["description"] and arm["kind"] in {"system", "baseline", "ablation"}


def test_study_runs_every_arm_under_the_same_seeds(client, study):
    status = client.get(f"/evaluation/blueprints/{study['blueprint_id']}/study").json()
    assert status["total_runs"] == 3 * 2
    assert status["complete"]
    seeds = harness.seeds_for(2)
    for label in (A.FULL_SYSTEM, A.FIXED_PROMPT_RAG, A.PLAIN_LLM):
        entry = status["arms"][label]
        assert entry["completed"] == 2
        assert sorted(entry["seeds"]) == sorted(seeds)  # shared seeds keep arms paired
        assert entry["candidates"] == len(ROWS) * 2


def test_arms_differ_only_in_what_the_model_is_told(study):
    """plain_llm gets no passages and no blueprint; the RAG baseline gets passages but still no
    blueprint; the full system gets both."""
    prompts = study["adapter"].prompts
    assert prompts
    with_blueprint = [u for _, u in prompts if u.startswith("Blueprint constraint:")]
    topic_only = [u for _, u in prompts if u.startswith("Topic:")]
    assert with_blueprint and topic_only

    ungrounded = [u for u in topic_only if "Course passages:" not in u]
    grounded_topic = [u for u in topic_only if "Course passages:" in u]
    assert ungrounded, "plain_llm must receive no passages"
    assert grounded_topic, "fixed_prompt_rag must receive passages but no blueprint"
    for user_prompt in topic_only:
        assert "Bloom level" not in user_prompt and "marks:" not in user_prompt


def test_unenforced_arms_are_still_measured(client, study):
    """An arm that does not enforce the blueprint still produces compliance reports, which is what
    the ablation comparison is made of."""
    report = client.get(f"/evaluation/blueprints/{study['blueprint_id']}/report").json()
    for label in (A.PLAIN_LLM, A.FIXED_PROMPT_RAG):
        arm = report["arms"][label]
        assert arm["first_draft_compliance"]["drafts"] == len(ROWS) * 2
        interval = arm["first_draft_compliance"]["rate"]
        assert 0.0 <= interval["low"] <= interval["point"] <= interval["high"] <= 1.0


def test_candidates_carry_their_arm_label_for_analysis(client, study):
    status = client.get(f"/evaluation/blueprints/{study['blueprint_id']}/study").json()
    assert set(status["arms"]) == {A.FULL_SYSTEM, A.FIXED_PROMPT_RAG, A.PLAIN_LLM}

    run_id = study["launched"]["launched"][0]["run_id"]
    candidate = client.get(f"/runs/{run_id}/candidates").json()[0]
    revealed = client.get(f"/evaluation/candidates/{candidate['question_id']}/arm",
                          headers=study["coordinator"]).json()
    assert revealed["arm_label"] in A.ARMS


def test_blinded_queue_hides_the_arm_and_is_stable_per_reviewer(client, study):
    reviewer = login(client, "reviewer1@example.edu")
    queue = client.get(f"/evaluation/blueprints/{study['blueprint_id']}/queue", headers=reviewer).json()
    assert len(queue) == len(ROWS) * 2 * 3

    # nothing in the response identifies the arm, not even a run to follow back to it
    for candidate in queue:
        assert candidate["run_id"] is None
        assert "arm_label" not in candidate

    again = client.get(f"/evaluation/blueprints/{study['blueprint_id']}/queue", headers=reviewer).json()
    assert [c["question_id"] for c in again] == [c["question_id"] for c in queue]  # stable order

    other = login(client, "reviewer2@example.edu")
    other_queue = client.get(f"/evaluation/blueprints/{study['blueprint_id']}/queue", headers=other).json()
    assert {c["question_id"] for c in other_queue} == {c["question_id"] for c in queue}
    assert [c["question_id"] for c in other_queue] != [c["question_id"] for c in queue]  # per reviewer


def test_reviews_in_a_study_are_recorded_as_blinded(client, study):
    """Reviewed through the queue, decisions record the blinded condition without the client
    having to claim it."""
    from app.db import SessionLocal
    from app.models import ReviewRecord
    from sqlalchemy import select

    reviewer = login(client, "reviewer1@example.edu")
    queue = client.get(f"/evaluation/blueprints/{study['blueprint_id']}/queue", headers=reviewer).json()
    for index, candidate in enumerate(queue):
        body = {"decision": "accept"} if index % 2 == 0 else {"decision": "reject", "reason": "weak"}
        assert client.post(f"/candidates/{candidate['question_id']}/review",
                           json=body, headers=reviewer).status_code == 200

    with SessionLocal() as db:
        flags = db.scalars(
            select(ReviewRecord.blinded).where(
                ReviewRecord.question_id.in_([c["question_id"] for c in queue])
            )
        ).all()
    assert flags and all(flags)


def test_report_carries_intervals_and_refuses_to_overclaim(client, study):
    report = client.get(f"/evaluation/blueprints/{study['blueprint_id']}/report").json()

    assert report["protocol"]["bootstrap_resamples"] == 10000
    assert report["protocol"]["pairing"] == "same constraint, same seed"

    for arm in report["arms"].values():
        rate = arm["acceptance_without_edits"]["rate"]
        assert rate["low"] <= rate["point"] <= rate["high"]
        assert arm["acceptance_by_seed"]  # variance across seeds is reported, not averaged away

    headline = report["headline"]
    assert headline["comparison"] == f"{A.FULL_SYSTEM} vs {A.FIXED_PROMPT_RAG}"
    assert headline["paired_questions"] == len(ROWS) * 2
    difference = headline["mcnemar"]["difference"]
    assert difference["low"] <= difference["point"] <= difference["high"]
    # the stub answers both arms identically, so the honest verdict is "no difference"
    assert difference["low"] <= 0 <= difference["high"]
    assert "no significant difference" in headline["verdict"]

    grounding = report["grounding"]
    assert grounding["citations"] > 0
    assert len(grounding["human_judgement_sample"]) <= 50
    assert "not a substitute" in grounding["note"]


def test_inter_rater_agreement_is_reported_once_two_reviewers_overlap(client, study):
    reviewer = login(client, "reviewer2@example.edu")
    queue = client.get(f"/evaluation/blueprints/{study['blueprint_id']}/queue", headers=reviewer).json()
    for index, candidate in enumerate(queue):
        body = {"decision": "accept"} if index % 3 else {"decision": "reject", "reason": "weak"}
        client.post(f"/candidates/{candidate['question_id']}/review", json=body, headers=reviewer)

    agreement = client.get(f"/evaluation/blueprints/{study['blueprint_id']}/report").json()["inter_rater_agreement"]
    assert agreement["shared_questions"] == len(ROWS) * 2 * 3
    assert agreement["kappa"]["low"] <= agreement["kappa"]["point"] <= agreement["kappa"]["high"]
    assert isinstance(agreement["weakly_grounded"], bool)
    if agreement["weakly_grounded"]:
        assert "weakly grounded" in agreement["note"]


def test_launching_an_unknown_arm_is_refused(client, study):
    r = client.post(f"/evaluation/blueprints/{study['blueprint_id']}/study",
                    json={"arms": ["not_an_arm"], "seeds": 1}, headers=study["coordinator"])
    assert r.status_code == 422
    assert "unknown arm" in r.json()["detail"]


def test_a_study_needs_a_frozen_blueprint(client, study, new_course):
    coordinator = study["coordinator"]
    draft = client.post(f"/courses/{study['course_id']}/blueprints", headers=coordinator).json()
    r = client.post(f"/evaluation/blueprints/{draft['blueprint_id']}/study",
                    json={"arms": [A.FULL_SYSTEM], "seeds": 1}, headers=coordinator)
    assert r.status_code == 422
    assert "frozen" in r.json()["detail"]
