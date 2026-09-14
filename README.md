# Assessment-Quality Platform — prototype

Vertical slice of `plan.md` §7: document → blueprint → grounded generation → compliance audit →
review → versioned question bank. One course, one coordinator, two reviewers.

```
backend/                FastAPI + SQLAlchemy + pgvector
  app/main.py           app, startup (tables + seed users/course)
  app/models.py         tables from plan.md §8 (+ users)
  app/routers/          endpoints from plan.md §9
  app/services/         ingestion, retrieval, compliance engine, orchestrator, review
  app/llm/              adapter interface + Ollama (self-hosted) and Anthropic (hosted) adapters
  tests/test_e2e.py     Day-5 end-to-end journey (stubbed LLM, real Postgres)
frontend/               React (Vite): 5-step guided flow (upload, blueprint, generate, review, bank)
storage/                uploaded source files (gitignored)
```

## Prerequisites

- PostgreSQL with the `vector` extension (pgvector 0.8.6 is built and installed on this machine
  for PostgreSQL 18; database `aqp` exists).
- Python 3.14, Node 24.
- An `ANTHROPIC_API_KEY` for the hosted LLM adapter.

## Run

```
cp .env.example backend/.env       # fill ANTHROPIC_API_KEY
cd backend && .venv/Scripts/activate && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev          # http://localhost:5173 (proxies /api -> :8000)
```

First start downloads the embedding model (`all-MiniLM-L6-v2`, 384 dims).

Login: with no OIDC issuer configured and `DEV_LOGIN=true`, the login screen lists the seeded
users (coordinator, reviewer1, reviewer2). Configure `OIDC_*` to use institutional SSO instead.

## Test

```
cd backend && .venv/Scripts/python -m pytest -q
```

Uses database `aqp_test` (created if missing) and a stub LLM adapter.

## Demo walkthrough (plan.md §7.1)

The UI is a five-step guided flow; each screen opens with a plain-language "what happens here" and
the step bar shows which steps are complete and which role performs them.

1. **Upload material** (Course Coordinator) — upload a PDF/DOCX unit. The table shows passages
   indexed and pages covered; *show sample passages* reveals the indexed text with its page.
2. **Define blueprint** (Coordinator) — New draft → add rows → *Save & validate* → *Freeze as
   version*. The field guide explains each column; validation flags inconsistent rows.
3. **Generate questions** (Faculty Reviewer) — pick the frozen version and *Start generation run*.
   The screen explains the retrieve → draft → audit → repair pipeline and shows progress.
4. **Review candidates** (Reviewer) — every card shows the blueprint row, the six checks with
   pass/fail and expected-vs-found, the draft history (what failed, what was repaired), and the
   source passages with page numbers and the quoted evidence highlighted. Accept / Edit / Reject,
   then *Publish* the accepted and edited ones.
5. **Question bank** — each published question with a readable provenance panel: blueprint
   version and row, model run (temperature, seed, prompt fingerprint), grounding, checks at
   publication, reviewer decision.

## Notes on what is recorded

- `generation_runs.temperature` / `seed` are logged per run. The Anthropic adapter reports both
  as `NULL`: current Claude models reject sampling parameters and the API has no seed parameter,
  so a value is never logged that the backend did not honour.
- `target_difficulty` has no deterministic rule on a candidate; it is validated for consistency
  with `bloom_level` at blueprint validation instead. The compliance engine checks `bloom_level`,
  `question_type`, `marks` (answer-length band), `concept`, `course_outcome` and `grounding`.
- Out of scope per plan §7.2: novelty engine, fitted ranker (candidates are ordered by blueprint
  position, then compliance), baselines/ablations, self-hosted LLM adapter.
