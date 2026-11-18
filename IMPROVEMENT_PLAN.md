# Improvement plan — robustness, CO-aligned novelty, interface

Written after reading the whole codebase (backend `app/`, the test suite, every frontend page) and
comparing it with `github.com/RudrakshChugh/NOVA` at `4c9cd7d`. The local tree is that commit plus
three extra inputs (`OLD papers/`, `unit2.pdf`, the user-stories PDF); no source file differs.

Everything here is local. Nothing is committed or pushed.

Status key: `[x]` done and verified, `[~]` done, not yet verified end to end, `[ ]` not done.

## 1. What the analysis found

### 1.1 Robustness gaps (each one traced to a line)

| # | Where | Problem |
|---|---|---|
| R1 | `orchestrator._generate_for_constraint` | `data["stem"]`, `data["options"]`, `cit["passage"] - 1` are read with no shape check. One malformed reply (missing key, `null` options, a passage number sent as `"1"`) raises and fails the **whole run**, discarding every other row. |
| R2 | `ollama_adapter`, `groq_adapter`, `anthropic_adapter` | `json.loads` on the model's text is unguarded, and Anthropic's `next(...)` raises a bare `StopIteration` when no text block comes back. The run's error then reads `JSONDecodeError: Expecting value`, which tells a coordinator nothing. |
| R3 | `orchestrator.execute_run` | One failed row fails the run. A blank candidate (`stem=""`, state `drafted`) is left behind in the review queue. |
| R4 | `main.lifespan` | Runs and ingestion jobs execute in background tasks. If the server restarts mid-run they stay `running` / `queued` forever; the Generate page then disables its button permanently ("A run is in progress"). |
| R5 | `routers/runs.generate` | No preflight. A run on a subject with no indexed material starts, drafts ungrounded questions and fails the grounding check on every row. Two runs can also be started on one blueprint at once. |
| R6 | `orchestrator` seed | Every row of a run is drafted with the same seed. Two blueprint rows with the same outcome, concept, level, marks and type therefore get an identical prompt and — on a backend that honours the seed — an **identical question**. |
| R7 | `frontend/api.js` | Errors are shown as `409: "blueprint must be frozen…"` (status code and JSON quotes). An expired session produces a 401 on every screen instead of returning to sign-in. A backend that is down shows `Failed to fetch`. |
| R8 | frontend | No error boundary: a render error blanks the app. |
| R9 | `tests/conftest.py` | The admin connection string is hard-coded, so the suite cannot run against any Postgres that is not `postgres:postgres@localhost:5432`. |

### 1.2 Novelty and outcome alignment gaps

| # | Where | Problem |
|---|---|---|
| N1 | `USER_TEMPLATE` | The model is told "begin the question with a verb at this level" but never which verbs the checker accepts (`compliance.BLOOM_CUES`). It is told the answer should be "proportional to the marks" but never the word band the checker enforces (`MARK_BANDS`). First drafts fail checks the model was never told about, and the repair budget is spent on it. |
| N2 | prompt | The course outcome is one bullet among five. Nothing says the question has to *assess* that outcome, so the outcome's words tend to be pasted into the answer key to satisfy the term-overlap check. |
| N3 | `novelty` | Novelty is purely reactive: draft, compare with past papers, redraft once. The model never sees what has already been asked until it has repeated it. |
| N4 | `novelty` | Only past-year papers are compared. Two candidates in the same run, or a candidate and a question already in the bank, can be near-identical and nothing notices (and R6 makes this likely). |
| N5 | `blueprints._validate` | A row can ask for a Bloom level above the level the linked course outcome is itself stated at (outcome says "Explain…", row says `create`). Nothing flags it. |

### 1.3 Interface

The interface is internally consistent (one token file, thin primitives) but deliberately austere:
Times New Roman throughout, 2–4px radii, no elevation, no motion. Structure and copy are good;
the visual layer is what holds it back. Concrete issues: the workflow has no visible "you are
here"; Generate shows a bare progress bar with no per-stage feedback and no check that the model
is reachable before a run is started; the novelty result is a table with no at-a-glance reading.

## 2. Plan

### Phase A — make it hard to break (backend)

- [x] A1 `llm/base.py`: `MalformedOutput(RuntimeError)`; all three adapters raise it with a readable message for unparseable or truncated output (R2).
- [x] A2 `orchestrator`: `normalise_draft()` validates and coerces a reply; a malformed reply is retried within `MALFORMED_RETRY_BUDGET` without spending the repair budget (R1).
- [~] A3 `orchestrator.execute_run`: on a normal run, a row that cannot be drafted is recorded on the run and skipped, its blank candidate removed, and the other rows still run. Evaluation runs keep failing whole, because a study with missing rows is not comparable (R3).
- [~] A4 `main.lifespan`: mark runs and ingestion jobs orphaned by a restart as failed, with a message that says so (R4).
- [~] A5 `routers/runs.generate`: refuse with 409 when the subject has no indexed passages or a run is already in progress on that blueprint (R5).
- [x] A6 `orchestrator`: per-row seed derived from the run seed and the row position — still deterministic and still shared across arms (R6).
- [~] A7 `routers/system.py`: `GET /system/status` — database, embedding model, LLM backend reachability, counts for the subject — for the Generate preflight.
- [~] A8 `tests/conftest.py`: derive the admin URL from `DATABASE_URL` (R9).

### Phase B — novel, outcome-aligned questions

- [x] B1 Prompt states the accepted cue verbs for the row's Bloom level and the answer-key word band, both read from `compliance` so prompt and checker cannot drift (N1).
- [x] B2 Prompt carries the outcome as its own block with an instruction to assess it, and a short novelty directive (fresh scenario / different aspect, not the passage's own example) (N2).
- [x] B3 Proactive novelty: the past-paper questions nearest the row are listed in the first prompt as "do not repeat". Only on arms that enforce novelty (N3).
- [x] B4 Sibling awareness: stems already drafted in this run for the same outcome or concept are listed in the prompt (N4).
- [~] B5 Internal duplicate audit: a draft is also scored — same four signals, same combiner — against earlier candidates of the run and published bank entries; a hit is redrafted within the novelty budget. `novelty_reports` gains `nearest_source` / `nearest_text` / `nearest_label` so the report says what it matched (N4).
- [x] B6 Blueprint validation returns advisory `warnings` when a row's Bloom level exceeds the level its outcome is stated at (N5).
- [x] B7 DB-free unit tests for B1–B4, B6 and A2.

### Phase C — interface

- [x] C1 New design system in `index.css`: same class names, new tokens — Inter for the interface, a serif kept only for question text, an indigo–violet accent, 10–16px radii, layered shadows, restrained motion, a tuned dark theme.
- [x] C2 Shell: branded sidebar, workflow as a connected stepper with completion, overall progress, cleaner top bar.
- [x] C3 Login: redesigned hero and sign-in panel.
- [x] C4 Generate: system-check panel (A7), pipeline strip, live run card.
- [x] C5 Candidate: novelty dial and "matched against" source, clearer check tiles.
- [x] C6 Blueprint: validation warnings (B6), outcome-coverage bars.
- [x] C7 `api.js`: readable errors, sign out on 401, clear "backend unreachable" message; an error boundary (R7, R8).

### Phase D — verify

- [x] D1 `npm run build` passes. `npm run lint` reports warnings only, of the kinds the codebase already had (`set-state-in-effect`, unused `user` props).
- [x] D2 `tests/test_generation_hardening.py` (48 tests) passes, with the two existing DB-free suites: 97 passed.
- [ ] D3 Full backend suite against Postgres + pgvector.
- [~] D4 App opened in a browser against fixture data: sign-in, Sources, Blueprint, Generate, Review (all four candidate tabs), light and dark, 1440px and 375px. Bank, Paper, Engines, Evaluation and People inherit the new tokens but were not opened with data in them.

## 3. Deliberately not done

- No change to the six compliance checks or their thresholds: the satisfaction rate is a reported
  metric and its denominator must not move.
- No LLM-judged "is this aligned with the outcome" check. The project's stated position is that the
  model never grades itself; outcome alignment is improved through the prompt and through
  blueprint validation instead.
- No offline "demo" LLM backend. Fabricated questions that look generated would be mistaken for
  the real thing.

## 4. Status (updated after the second pass)

Phase A and B items marked `[~]` above are now verified: the integration suite runs against a real
PostgreSQL with pgvector (`backend/scripts/dev_db.py`, no system install), and
`tests/test_generation_pipeline.py` exercises each of them against the database.

### Added in the second pass

- **Local database with nothing to install** — `pip install pixeltable-pgserver`, then
  `python backend/scripts/dev_db.py`. It prints the `DATABASE_URL` for the app and for the tests.
- **Past-paper splitter** — two of the five real UCS503 papers used to come out as a single
  "question" (bare-number numbering with marks on their own line; a grid whose `Q2a` markers sit
  alone on a line). They now split into 10 and 5 questions; the corpus went from 25 to 38.
- **Coverage guard** — a blueprint row whose concept the indexed material does not cover is warned
  about at validation and skipped by a run instead of being drafted from unrelated passages.
  Found on real data: `unit2.pdf` has nothing on function points, yet retrieval still returned
  its "top five" (title slides, at 0.37 similarity).
- **Interface, third version** — bold style chosen from ten mock-ups: ink outlines, flat colour,
  hard shadows; a top stepper instead of a sidebar; a dashboard home; help text collapsed by
  default; wordmark only, no logo tile.

### Verified

- Backend suite: **202 passed** on the final tree, against real PostgreSQL with pgvector. The
  suite pins the LLM backend to an unreachable address, so no test can call a hosted model.
- Four real runs on `groq:openai/gpt-oss-120b` over the same six-row blueprint: each drafted five
  of six rows in 28-72 seconds. The last run was 5/5 compliant on the first draft with no
  repairs, 20 of 21 quotes verified verbatim, and none of the two defects the earlier runs
  showed (citation markers left in answer keys; "describe how you would..." stems).
- Real data, real backend: `unit2.pdf` indexed (467 passages); five past papers loaded (38
  questions); duplicate scoring on real past questions — a verbatim repeat scores 1.00 and the
  same question with its numbers changed 0.90 (both flagged at 0.85); a reworded version scores
  0.65 and is **not** flagged by the unfitted placeholder combiner.
- Real model: a six-row blueprint run on `groq:openai/gpt-oss-120b` produced five candidates that
  passed all six checks, and skipped the function-point row with the reason named.
- Interface: sign-in, Home, Sources, Blueprint, Generate and Review looked at in a browser against
  the real backend and the real Groq candidates, light and dark, desktop and phone width.

### Known limits — stated, not hidden

- **The novelty combiner is not fitted.** Until the pair set is built and the combiner trained on
  the Engines page, the duplicate score is an equal-weight average with a hand-set 0.85 line. It
  catches copies and number swaps; it does not catch a thorough rewording.
- **"Same question, new setting" is not solved.** Two rows with the same outcome, concept and
  level still came back as one task in two settings on the last real run (a bookstore and a
  library, scored 0.68 against a 0.85 line). The prompt asks for a different task and setting
  and blueprint validation now warns about such twin rows, but the audit does not catch it.
- **Bloom level is judged from the opening verb only.**
- **Nobody has checked the generated questions for factual correctness.** The checks cover
  format, level, length, outcome terms, grounding quotes and novelty — not whether the answer
  key is right. That is what the review step is for.
- **The coverage floor (0.40) is an embedding threshold** set from two measurements. A row near
  the line can be skipped wrongly; the run names it and the setting can be lowered.
- Bank, Paper, Engines, Evaluation and People use the new design tokens but were not opened with
  data in them.
- `npm run lint` reports warnings only (`set-state-in-effect`, unused props), no errors.

## 4b. Third pass — the limits listed above, revisited

Each item below was a "known limit" after the second pass. What was done, and what a real run
on `groq:openai/gpt-oss-120b` over the real `unit2.pdf` then showed.

| Limit | What was done | Measured result |
|---|---|---|
| Twin rows gave one task in two settings | Rows sharing outcome, concept and level are each assigned a different *form of task* from a fixed list per Bloom level, and a different share of the retrieved passages (`orchestrator.TASK_FORMS`, `_passages_for`). | The two DFD rows came back as "find the errors in a given diagram" and "compare two given designs". Similarity between them fell from 0.57–0.68 to 0.04–0.42. |
| Reworded past questions passed | The novelty model was fitted on the real papers (139 pairs from 38 questions). Selection now prefers the model that separates the classes most widely and puts the threshold in the middle of the gap, not on its edge. | Verbatim copy 1.00, numbers changed 1.00, reworded 0.61, paraphrase 0.46 — all flagged at 0.25. New questions 0.00–0.01. Held-out pairs: 12 of 12 duplicates caught, 0 of 11 non-duplicates flagged. |
| Bloom judged by the first verb only | From `apply` upwards, a stem that opens with the right verb but asks to "describe how you would…/outline the steps" fails the Bloom check (`compliance.procedure_ask`). | No such stem in the last two real runs. |
| Nobody checked facts | Evidence check (`services/claims.py`): the model lists each general statement in the answer key with the sentence of the material behind it; the program verifies the sentence is there, word for word. An answer with unbacked statements is rewritten once. Shown in the review screen's Evidence tab. | It caught an answer resting on two DFD rules that are true but are not in the slides, and the rewrite came back 3 of 3 backed. |
| A bare "(1)" was stripped from answers | Removed; only "(passage n)" and "(cite …)" forms are stripped. | Covered by a test. |
| Printed paper did not look like the institute's | `paper_pdf.py` rewritten: roll-number box, institution and department, examination/course block, note, one ruled table. A per-subject option prints the CO and BL columns on the candidates' paper, as the real UCS503 papers do. | `sample-output/UCS503-mid-semester-paper.pdf`, produced through the app from real generated questions. |

Backend suite: **229 passed**.

### What is still true, and should be said to anyone using it

- **The evidence check is conservative and not deterministic.** It depends on a model copying a
  sentence exactly. Re-checking the same six answers twice gave different counts, and it reported
  "not found" for statements that are in the slides (the slide says "A use case diagram establish
  the capability of the system as a whole"; the check missed it when the answer combined it with
  another clause). A red mark means *check this*, not *this is wrong*. Sometimes the call fails
  and there is no report at all.
- **It does not check the scenario a question invents, or MCQ keys, or arithmetic.**
- **The fitted novelty model lives in `backend/data/models/` and is per subject.** It is not in
  git. On any new deployment or subject, build the pair set and train on the Engines page; until
  then the placeholder is used. The held-out figure above is from 23 pairs — encouraging, small.
- **Bloom is still verb-based.** The new rule closes one specific loophole; it does not measure
  cognitive demand.
- **Generation is slower.** The evidence check and its one rewrite took the six-row run from
  about two minutes to about five.
- **Human review remains the correctness check.** Nothing here replaces reading the answer key.

## 5. Merging

The folder is a git repository on branch `nova-improvements`, based on `origin/main` at `4c9cd7d`
of `github.com/RudrakshChugh/NOVA`. Nothing is committed and nothing is pushed.

- `backend/.env` (which holds the API key), `backend/.localdb/`, `.claude/`, `design-previews/`,
  `unit2.pdf`, `OLD papers/` and the user-stories PDF are in `.gitignore`.
- No new production dependency on the backend. The frontend adds `@fontsource-variable/inter`
  and `@fontsource-variable/space-grotesk`; `package-lock.json` is updated to match.
- Three columns are added to `novelty_reports` by the existing startup `SCHEMA_PATCHES`
  mechanism, so a deployed database migrates itself on the next start.
- The API key was pasted into a chat. Rotate it in the Groq console before the project is
  deployed anywhere.
