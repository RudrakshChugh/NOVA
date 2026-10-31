"""Day 5 end-to-end journey (plan §10): upload -> blueprint -> generate -> review -> publish.

Runs against a real Postgres database (aqp_test) so ingestion, pgvector retrieval and every
table write are exercised. The LLM adapter is stubbed so the test is deterministic and does
not need an API key; live generation is verified manually against the hosted backend.
"""

import pytest

from app.llm.base import GenerationResult
from app.services import orchestrator

from .conftest import login, write_pdf

PASSAGE = (
    "Congestion control in TCP limits the sending rate when the network is overloaded. "
    "TCP uses a congestion window that grows additively and shrinks multiplicatively. "
    "Slow start doubles the window every round trip until a threshold is reached. "
    "When a packet loss is detected the window is halved. "
    "Fast retransmit resends a segment after three duplicate acknowledgements. "
    "Flow control, by contrast, protects the receiver rather than the network. "
    "The receiver advertises a window that bounds how much unacknowledged data may be in flight."
)


class StubAdapter:
    """First draft deliberately violates bloom_level and cites a quote that is not in the passage;
    the repair draft complies."""

    backend_id = "stub:test"

    def __init__(self):
        self.calls = 0

    def generate(self, req):
        self.calls += 1
        repair = "previous draft violated" in req.user
        stem = "Explain how TCP congestion control reacts to packet loss." if repair else "List two TCP mechanisms."
        answer = ("TCP halves the congestion window on loss and re-enters additive growth; slow start doubles the window "
                  "each round trip until the threshold, and fast retransmit resends after three duplicate acknowledgements.")
        quote = "When a packet loss is detected the window is halved." if repair else "TCP halves its window on every loss event."
        return GenerationResult(
            data={"stem": stem, "answer_key": answer, "options": [], "citations": [{"passage": 1, "quote": quote}]},
            model_version="stub", temperature=req.temperature, seed=req.seed,
        )


@pytest.fixture
def stub(monkeypatch):
    adapter = StubAdapter()
    monkeypatch.setattr(orchestrator, "get_adapter", lambda: adapter)
    return adapter


def test_full_journey(client, stub, tmp_path):
    coordinator = login(client, "coordinator@example.edu")
    reviewer = login(client, "reviewer1@example.edu")
    course_id = client.get("/courses").json()[0]["course_id"]

    # 1-3. upload and ingest a real PDF
    pdf = write_pdf(tmp_path / "unit1.pdf", PASSAGE)
    with pdf.open("rb") as f:
        r = client.post(f"/courses/{course_id}/materials", files={"file": ("unit1.pdf", f, "application/pdf")}, headers=coordinator)
    assert r.status_code == 201, r.text
    assert r.json()["chunk_count"] >= 1 and r.json()["pages_covered"] == 1
    assert client.get(f"/courses/{course_id}/materials").json()[0]["chunk_count"] == r.json()["chunk_count"]

    # reviewer cannot upload
    with pdf.open("rb") as f:
        assert client.post(f"/courses/{course_id}/materials", files={"file": ("x.pdf", f)}, headers=reviewer).status_code == 403

    # 4. blueprint: draft -> constraints -> validate -> freeze
    bp = client.post(f"/courses/{course_id}/blueprints", headers=coordinator).json()
    rows = [{"course_outcome": "CO1 Explain transport-layer congestion control", "concept": "TCP congestion control",
             "bloom_level": "understand", "marks": 5, "question_type": "short_answer", "target_difficulty": "hard"}]
    client.put(f"/blueprints/{bp['blueprint_id']}/constraints", json=rows, headers=coordinator)
    v = client.post(f"/blueprints/{bp['blueprint_id']}/validate").json()
    assert not v["ok"] and "inconsistent" in v["issues"][0]
    assert client.post(f"/blueprints/{bp['blueprint_id']}/freeze", headers=coordinator).status_code == 422
    rows[0]["target_difficulty"] = "easy"
    client.put(f"/blueprints/{bp['blueprint_id']}/constraints", json=rows, headers=coordinator)
    assert client.post(f"/blueprints/{bp['blueprint_id']}/validate").json() == {
        "ok": True, "issues": [], "total_marks": 5, "outcomes": {rows[0]["course_outcome"]: 5}}
    frozen = client.post(f"/blueprints/{bp['blueprint_id']}/freeze", headers=coordinator).json()
    assert frozen["version"] == 1 and frozen["frozen_at"]
    assert client.put(f"/blueprints/{bp['blueprint_id']}/constraints", json=rows, headers=coordinator).status_code == 409

    # 5-6. generate (background task completes inside TestClient), compliance catches the first draft and repairs it
    run = client.post(f"/blueprints/{bp['blueprint_id']}/generate", headers=reviewer).json()
    run = client.get(f"/runs/{run['run_id']}").json()
    assert run["status"] == "completed", run["error"]
    assert run["backend_id"] == "stub:test" and len(run["prompt_hash"]) == 64
    assert run["temperature"] == 0.7 and isinstance(run["seed"], int)
    assert stub.calls == 2

    # 7. results carry citation with page ref and compliance evidence
    cands = client.get(f"/runs/{run['run_id']}/candidates").json()
    assert len(cands) == 1
    q = cands[0]
    assert q["state"] == "novel" and q["repair_attempts"] == 1
    assert q["novelty"] is None  # no past-year corpus for this course, and the run says so
    assert q["citations"][0]["page_ref"] == "p1" and q["citations"][0]["char_span"] != "not_found"
    assert q["compliance"]["is_compliant"]
    ev = client.get(f"/candidates/{q['question_id']}/evidence").json()
    assert len(ev["reports"]) == 2
    first = {v["field"]: v for v in ev["reports"][0]["violations"]}
    assert set(first) == {"bloom_level", "grounding"}
    assert first["bloom_level"]["expected"] == "understand" and first["bloom_level"]["observed"] == "remember"
    assert first["grounding"]["observed"] == "no citation with a verbatim quote"
    assert ev["reports"][1]["is_compliant"]

    # 8-9. review: edit with edit distance, then summary
    assert client.post(f"/candidates/{q['question_id']}/review", json={"decision": "reject"}, headers=reviewer).status_code == 422
    edited = q["stem"].replace("packet loss", "a lost packet")
    r = client.post(f"/candidates/{q['question_id']}/review", json={"decision": "edit", "edited_stem": edited}, headers=reviewer).json()
    assert r["state"] == "edited" and 0 < r["review"]["edit_distance"] < 0.3
    s = client.get(f"/runs/{run['run_id']}/reviews").json()
    assert s["edited"] == 1 and s["pending"] == 0 and s["mean_edit_distance"] == r["review"]["edit_distance"]

    # 10. publish with provenance
    entries = client.post("/candidates/publish", json={"question_ids": [q["question_id"]]}, headers=reviewer).json()
    assert entries[0]["version"] == 1
    snap = client.get(f"/bank/entries/{entries[0]['entry_id']}").json()["snapshot"]
    assert snap["stem"] == edited and snap["original_stem"] == q["stem"]
    assert snap["blueprint"]["version"] == 1 and snap["run"]["run_id"] == run["run_id"]
    assert snap["citations"][0]["page_ref"] == "p1" and snap["review"]["decision"] == "edit"
    assert client.get(f"/runs/{run['run_id']}/candidates").json()[0]["state"] == "published"
    assert client.post("/candidates/publish", json={"question_ids": [q["question_id"]]}, headers=reviewer).status_code == 409


# --- access control ---------------------------------------------------------------------------


def test_data_routes_refuse_an_unsigned_request(client):
    """On a deployed instance the URL is public, so every data route has to turn away a caller with
    no session. Only the sign-in endpoints are reachable without one.

    An empty Authorization header overrides the session the fixture attaches, which is the closest
    a shared client can get to sending no header at all."""
    anon = {"Authorization": ""}
    for path in ("/courses", "/bank/entries", "/novelty/model", "/evaluation/arms"):
        assert client.get(path, headers=anon).status_code == 401, path
    assert client.post("/auth/login", json={"email": "coordinator@example.edu"}, headers=anon).status_code == 200


def test_a_user_without_a_role_is_refused_everywhere(client):
    """A `pending` account authenticates but may do nothing: it has no role until a coordinator
    grants one, and review decisions from an unapproved account would land in the study data."""
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        user = User(email="newcomer@example.edu", name="Newcomer", role="pending")
        db.add(user)
        db.commit()
        token = __import__("app.auth", fromlist=["issue_token"]).issue_token(user)

    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/courses", headers=headers).status_code == 403
    assert client.get("/bank/entries", headers=headers).status_code == 403
    # ...but the session itself is valid, which is what lets the app show "awaiting approval".
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["role"] == "pending"


def test_the_authorization_url_carries_one_signed_state(client, monkeypatch):
    """A second `state` parameter would make which value Google echoes back ambiguous, and the
    callback would reject a sign-in that was perfectly valid."""
    from urllib.parse import parse_qs, urlparse

    from app.config import settings
    from app.routers import auth as auth_router

    monkeypatch.setattr(settings, "oidc_issuer", "https://accounts.google.com")
    monkeypatch.setattr(settings, "oidc_client_id", "test-client")
    monkeypatch.setattr(settings, "oidc_client_secret", "test-secret")
    monkeypatch.setattr(settings, "allowed_domain", "thapar.edu")
    monkeypatch.setattr(
        auth_router, "_discovery",
        lambda _issuer: {"authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth"},
    )

    r = client.get("/auth/login", follow_redirects=False)
    assert r.status_code == 303
    params = parse_qs(urlparse(r.headers["location"]).query)
    assert len(params["state"]) == 1, "exactly one state parameter"
    assert params["hd"] == ["thapar.edu"]  # restricts Google's own account picker
    # ...and the callback must accept that state, which is what a second copy would have broken.
    auth_router._state_serializer.loads(params["state"][0], max_age=auth_router.STATE_MAX_AGE)


def test_the_failover_probe_never_raises(monkeypatch):
    """A deployment leaves OLLAMA_URL unset until a tunnel exists, and a typo in it is likelier
    still. Either way the probe means 'not reachable', and must not fail a generation run."""
    from app.config import settings
    from app.llm.failover import self_hosted_reachable

    for url in ("", "   ", "not a url", "http://[::bad", "ftp://nowhere", "http://127.0.0.1:1"):
        monkeypatch.setattr(settings, "ollama_url", url)
        assert self_hosted_reachable() is False, url


def test_a_provider_connection_string_is_normalised_to_psycopg3():
    """Hosted providers hand out `postgresql://`, which SQLAlchemy resolves to psycopg2 — a driver
    this project does not install. Pasting the string as given must still work."""
    from app.config import Settings

    given = "postgresql://user:pw@ep-x.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    assert Settings(database_url=given).database_url == (
        "postgresql+psycopg://user:pw@ep-x.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    # the older scheme some providers still emit
    assert Settings(database_url="postgres://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"
    # an explicit driver is left exactly as written
    explicit = "postgresql+psycopg://u:p@h/db"
    assert Settings(database_url=explicit).database_url == explicit
