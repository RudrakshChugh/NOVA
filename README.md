# Assessment-Quality Platform — prototype

Vertical slice of `plan.md` §7: document → blueprint → grounded generation → compliance audit →
review → versioned question bank. One course, one coordinator, two reviewers.

```
backend/                FastAPI + SQLAlchemy + pgvector
  app/main.py           app, startup (tables + seed users/course)
  app/models.py         tables from plan.md §8 (+ users)
  app/routers/          endpoints from plan.md §9
  app/services/         ingestion, retrieval, compliance engine, orchestrator, review
  app/llm/              adapter interface + hosted (Anthropic) adapter
  tests/test_e2e.py     Day-5 end-to-end journey (stubbed LLM, real Postgres)
frontend/               React (Vite): login, materials, blueprints, generate & review, bank
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

1. Log in as **Course Coordinator** → *Materials* → upload a PDF/DOCX unit. The table shows
   chunks indexed and pages covered.
2. *Blueprints* → New draft → add rows (two course outcomes, mixed Bloom levels and marks) →
   Save rows → Validate → Freeze as version.
   To show the compliance check catching a violation, include a row whose Bloom level the model
   tends to drift from (e.g. `evaluate` on a mostly descriptive passage) — the badge names the
   violated field and the evidence panel shows the first draft's report before repair.
3. Log out, log in as **Faculty Reviewer 1** → *Blueprints* → select the frozen version →
   Generate questions. The run page polls until complete.
4. Each candidate shows the cited passage with page reference and support score, the compliance
   badge (or the violated fields with expected/observed), the answer key and repair count.
5. Accept / Edit (edit distance is computed) / Reject (reason required). The summary updates.
6. Publish accepted and edited candidates → *Question Bank* → *provenance* shows the snapshot:
   blueprint version, run id, backend, prompt hash, citations, compliance, reviewer decision.

## Notes on what is recorded

- `generation_runs.temperature` / `seed` are logged per run. The Anthropic adapter reports both
  as `NULL`: current Claude models reject sampling parameters and the API has no seed parameter,
  so a value is never logged that the backend did not honour.
- `target_difficulty` has no deterministic rule on a candidate; it is validated for consistency
  with `bloom_level` at blueprint validation instead. The compliance engine checks `bloom_level`,
  `question_type`, `marks` (answer-length band), `concept`, `course_outcome` and `grounding`.
- Out of scope per plan §7.2: novelty engine, fitted ranker (candidates are ordered by blueprint
  position, then compliance), baselines/ablations, self-hosted LLM adapter.
