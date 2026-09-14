# Plan: Assessment-Quality Platform — Prototype

## What this document is

This is the full technical and execution plan for the prototype of the
Assessment Blueprint Compliance, Novelty Auditing and Reviewer-Adapted Question
Bank Platform. It is written to be self-contained: any reader or system that
reads this file should understand what we are building, how every part works,
what the prototype covers, what it does not, and how each decision traces back
to a project requirement.

---

## 1. Problem statement

Faculty members set exam question papers manually. Each question must map to a
course outcome, sit at the right Bloom level, match the mark weighting, be
answerable from what was taught, and not repeat past papers. All of this is
currently verified by memory and manual comparison.

Three things go wrong repeatedly:

1. Blueprint drift: questions do not fully cover the stated outcomes, or land at
   the wrong Bloom level, and nobody catches it until after the exam.
2. Novelty failure: a question that has been paraphrased, renumbered, or had its
   conditions reordered passes manual inspection because it reads as new, even
   though it is substantially a duplicate of a past-year question.
3. Reviewer knowledge is lost: every semester starts from scratch because there
   is no record of which questions were accepted, rejected or edited, or why.

Generic AI tools (ChatGPT, NotebookLM, basic RAG) produce fluent questions but
guarantee none of the above. They do not check a blueprint, do not compare
against institutional past papers, and do not learn from reviewer behaviour.

---

## 2. Intended users

- Faculty members who set and moderate question papers.
- Course coordinators responsible for ensuring outcome coverage across a paper.
- Examination cells accountable for overall paper quality and fairness.

For the prototype, we work with one course, one coordinator, and two faculty
reviewers.

---

## 3. Proposed system (full scope)

A hosted platform that generates exam questions under explicit constraints and
audits them before a human sees them.

### 3.1 Three core contributions

**Blueprint compliance engine.** The coordinator defines a blueprint specifying
course outcome, concept, Bloom level, marks, question type and target difficulty
for each required question. Every generated candidate is checked against every
constraint. Violations are named by field, expected value and observed value.
This is rule-based validation plus a Bloom-level classifier, deliberately not
the LLM grading its own output.

**Multi-signal novelty engine.** Each candidate is compared against the
historical past-year question corpus using four signals combined:
- Semantic: embedding cosine similarity.
- Lexical: BM25 or TF-IDF overlap.
- Structural: comparison over extracted PYQ metadata (type, marks, constraint
  ordering).
- Numerical: check for questions that differ only in their numbers.

The four signal scores are combined by a logistic regression over the signal
vector to produce a single duplicate probability. This model class is chosen
because it is interpretable (the learned weight per signal is directly
reportable), stable at small sample sizes, and fast enough to run in the
generation loop. The labelled duplicate pair set is split 60/20/20 into train,
validation and test folds, stratified by difficulty of the pair (easy surface
duplicates vs hard paraphrase/renumber cases). Threshold tuning and any
hyperparameter selection use only the validation fold. The test fold is
touched exactly once, for the numbers reported in the paper. A single
embedding threshold is explicitly insufficient (per project requirements)
because paraphrased or renumbered questions sit below it.

**Reviewer-adapted ranker.** Every accept, reject and edit by a faculty reviewer
is stored as a structured record with decision label, reviewer identity, edited
text (if any), computed edit distance, and timestamp. A gradient-boosted model
(LightGBM) is fitted on these records to score and rank future candidates by
predicted acceptance. This is not few-shot prompting or pasting past examples
into a context window; it is a separately trained preference model.

Ranker performance is compared against two floor baselines: a logistic
regression over the same features, and a compliance-score-only baseline that
ranks candidates by their blueprint satisfaction rate alone. If LightGBM does
not beat both floors on held-out review records, the simpler model ships and
we report that result honestly. This closes the small-data overfitting risk:
with limited review volume, a complex model that cannot outperform a
single-feature baseline is not a contribution.

Feature leakage check: no feature derived from the reviewer's decision on the
same question (acceptance label, edit distance, reject reason) is included in
the training features for that question. The ranker sees only pre-review
attributes: compliance scores, novelty signals, Bloom level, marks, question
length, grounding coverage and citation count.

### 3.2 End-to-end workflow

1. Coordinator uploads course material (PDF or Word) and historical PYQs.
2. System parses, chunks, embeds and indexes the material; extracts structured
   metadata from PYQs.
3. Coordinator defines and freezes a versioned blueprint.
4. Faculty reviewer selects a blueprint and triggers generation.
5. System retrieves grounding passages, drafts candidates through a swappable
   LLM backend, runs blueprint compliance audit, runs novelty audit, and ranks
   survivors.
6. Reviewer sees ranked candidates with citations, page references, compliance
   badges and novelty signal breakdowns.
7. Reviewer accepts, edits or rejects each candidate.
8. Structured review records are written; ranker is refitted when enough
   accumulate.
9. Accepted questions are published to a versioned question bank with full
   provenance.

### 3.3 Evaluation protocol

#### 3.3.1 Pre-registration

The evaluation protocol is frozen before any reviewer sessions begin. The
blueprint used for the comparison is authored and versioned before any system
generates against it, and is not modified after seeing outputs. If the
blueprint needs correction (a constraint is found to be malformed), the
correction is logged and a new version is frozen; the comparison re-runs from
the new version. No result is reported against a blueprint that was tuned
after seeing reviewer reactions.

#### 3.3.2 Baselines (pinned configurations)

Each baseline is fully specified so the comparison is reproducible:

- **Plain LLM:** the same hosted model and version as the full system,
  temperature 0.7, seed fixed per constraint, no retrieval context, no
  blueprint in the prompt, no novelty check. Prompt template committed to
  the repository before the run.
- **Source-grounded generation:** NotebookLM (or, if unavailable, the same
  hosted model with the full course material in context and no blueprint or
  novelty layer). The exact tool version and date of access are recorded.
- **Fixed-prompt RAG:** the same retrieval pipeline (pgvector, same embedding
  model, same chunk size), the same hosted LLM and version, a fixed prompt
  template committed to the repository, no blueprint enforcement, no novelty
  check, no ranker.
- **Full system:** the complete pipeline with blueprint enforcement, novelty
  auditing and reviewer-adapted ranking.

All four are run against the same frozen blueprint, the same course material
index, and the same PYQ corpus.

#### 3.3.3 Variance control

LLM generation is stochastic. Every generation call logs the model version,
temperature, and random seed. For the evaluation comparison, each condition
is run three times per constraint with different seeds. Reported metrics are
the mean across runs with a 95% bootstrap confidence interval. If the
confidence interval on the difference between the full system and a baseline
overlaps zero, we report that as no significant difference rather than
claiming an improvement.

#### 3.3.4 Reviewer blinding

During the evaluation comparison, the two reviewers see questions from all
four baselines and all ablation arms shuffled together in a single review
session, with no label indicating which system produced which question.
Question order is randomised per reviewer. The system logs which arm produced
each question, but this label is hidden from the review interface and
revealed only at analysis time. This prevents bias toward the system the
reviewers helped build.

Blinding is not applied during normal platform use outside the evaluation
(when there is only one system generating), since there is nothing to blind
against.

#### 3.3.5 Ablation design

Each ablation removes one engine and holds everything else constant:

- **No blueprint enforcement:** compliance checking and repair loop removed;
  candidates go straight from grounding to novelty audit.
- **No novelty engine:** multi-signal scoring removed; all grounded,
  compliant candidates survive to ranking.
- **No reviewer adaptation:** ranker replaced with the compliance-score-only
  floor baseline; candidates are ordered by compliance rate rather than
  learned preference.

The first two ablations change which candidates survive (filtering), so they
are evaluated on the same headline metric: acceptance without edits. The
third ablation changes only candidate ordering, not which candidates appear.
Acceptance without edits is insensitive to order, so the ranker ablation
uses a ranking-specific metric: NDCG@k computed over reviewer decisions,
where k is the number of questions in the blueprint. This measures whether
the ranker puts the questions the reviewer would accept near the top,
which is the actual claim the ranker makes.

#### 3.3.6 Reported metrics and statistical tests

**Headline metric:** faculty acceptance without edits (proportion of
generated questions accepted as-is). Compared between the full system and
the fixed-prompt RAG baseline using McNemar's test on paired binary outcomes
(each question is either accepted or not, and the same constraint is
generated by both systems on the same blueprint). Reported with a 95%
bootstrap confidence interval on the difference.

**Novelty engine:** duplicate-detection precision, recall and F1 on the
held-out test fold of the labelled pair set. Reported with 95% bootstrap
confidence intervals on each.

**Blueprint compliance:** constraint satisfaction rate (proportion of
generated candidates that pass all constraint checks on the first draft,
before any repair loop). Reported as a proportion with a Wilson score
interval.

**Grounding and citation correctness:** proportion of cited passages that
a human judge confirms actually support the claim in the question. Measured
on a random sample of 50 citations, reported with a Wilson score interval.

**Edit distance:** mean normalised Levenshtein distance on questions that
were edited rather than accepted or rejected. Reported with standard
deviation.

**Inter-rater agreement:** Cohen's kappa between the two reviewers,
computed on the subset of questions both reviewers rate. Reported with a
95% confidence interval (bootstrap). If kappa is below 0.4, we flag the
acceptance metric as weakly grounded and discuss the implication rather
than suppressing the number.

**Ranker ablation:** NDCG@k on the ranked list, compared between the
LightGBM ranker and the compliance-score-only floor. Reported with
bootstrap confidence interval.

All confidence intervals are computed via 10,000 bootstrap resamples. No
p-value is reported without its corresponding effect size and interval.

---

## 4. Technical stack

| Layer               | Technology                                         |
|---------------------|----------------------------------------------------|
| Frontend            | React                                              |
| Backend API         | FastAPI (Python)                                   |
| Database            | PostgreSQL with pgvector extension                 |
| Object storage      | Local disk or S3-compatible (source files)         |
| Ingestion / parsing | PyMuPDF (PDF), python-docx (Word)                  |
| Chunking            | Sentence-window chunker                            |
| Embeddings          | sentence-transformers                              |
| Retrieval           | pgvector similarity search                         |
| LLM generation      | Adapter interface, two implementations:            |
|                     |   - Hosted API adapter (development)               |
|                     |   - Self-hosted open-weight adapter (portability)  |
| Blueprint engine    | Rule-based validation + Bloom-level classifier     |
| Novelty engine      | Cosine + BM25 + structural + numerical signals     |
|                     | combined by logistic regression                    |
| Ranker              | LightGBM gradient-boosted model                    |
| Authentication      | Institutional SSO via OIDC                         |
| Hosting             | Containerised on a single VM, portable             |

Key decisions:
- Postgres with pgvector instead of a separate vector database, because the
  corpus is small and one fewer service to run reduces deployment risk.
- The LLM backend is behind an adapter interface, not called directly, so the
  core does not depend on any single vendor remaining available or unchanged.
- The blueprint engine does not use the LLM to grade its own compliance; it is
  rule-based so its behaviour is deterministic and auditable.
- The ranker is a small gradient-boosted model, not a neural one, because our
  review data volume will not justify a larger architecture. If it cannot beat
  a logistic-regression floor, we ship the simpler model.
- The novelty signal combiner is logistic regression rather than a learned
  ensemble, because interpretability of per-signal weights is more valuable
  than marginal accuracy at our data scale.

---

## 5. Data requirements

**Institutional (non-negotiable):**
- Course material for one subject: lecture notes, slides or textbook chapters as
  PDF or Word.
- Historical past-year questions for the same subject: at least two years.
- Access to at least two faculty reviewers who will give repeated review sessions.

**Hand-labelled (we produce this ourselves):**
- A set of question pairs labelled duplicate or non-duplicate, including hard
  negatives: real PYQs that have been paraphrased, renumbered, or reordered.
  Target: 300 pairs minimum. This number is chosen so that the 20% test fold
  contains at least 60 pairs, which is the minimum needed for a bootstrap
  confidence interval on F1 to be narrower than +/- 0.10 at 95% confidence.
  Quality matters more than quantity, but 300 is the floor below which the
  interval becomes too wide to distinguish our engine from a single-threshold
  baseline.

**Data splits (committed before any model training):**
- Novelty engine labelled pairs: 60% train, 20% validation, 20% test.
  Stratified by pair difficulty (surface duplicate vs hard paraphrase/renumber).
  The split is randomised once, committed to the repository as a seed and a
  split file, and never re-drawn.
- Ranker review records: leave-one-session-out cross-validation during
  development (each review session is one fold, so the model never trains on
  decisions from the same sitting it predicts on). Final reported number uses
  the last chronological session as a held-out test set, so temporal leakage
  is impossible.
- Bloom-level classifier: if trained on labelled Bloom examples, the same
  60/20/20 protocol applies. If rule-based (keyword + structure heuristics),
  no split is needed but the rule set is committed before evaluation.

**Public (component checks only, not a substitute):**
- SQuAD and RACE can be used to sanity-check the grounding and retrieval
  pipeline, but they do not replace institutional data and cannot be presented
  as the evaluation dataset.

---

## 6. System architecture (summary)

### 6.1 Actors

Three human actors:
- Course Coordinator: uploads material, uploads PYQs, defines blueprints.
- Faculty Reviewer: generates questions, reviews, accepts/rejects/edits,
  publishes.
- Research Evaluator: runs baselines, runs ablations, reads evaluation dashboard.

Seven external systems:
- Identity Provider (SSO)
- Content and PYQ Repository (object store)
- Retrieval Index (pgvector)
- LLM Generation Backend (behind adapter)
- Historical PYQ Corpus (indexed in pgvector)
- Review Record Store (Postgres)
- Versioned Question Bank (Postgres)

### 6.2 Services

- API Gateway: auth, rate limiting, audit logging.
- Ingestion Service: parse, chunk, embed, extract PYQ metadata.
- Blueprint Service: validate, freeze, version blueprints.
- Generation Orchestrator: run lifecycle, calls retrieval, LLM, blueprint
  engine, novelty engine, ranker in sequence.
- Blueprint Engine: rule-based constraint checking.
- Novelty Engine: four-signal scoring against PYQ corpus.
- Ranker Service: fit and score on review records.
- Review Service: handle decisions, compute edit distance, trigger refit.
- Question Bank Service: version and snapshot published entries.
- Evaluation Harness: drive baseline and ablation runs, compute metrics.

### 6.3 Data stores

- Postgres (relational): blueprints, runs, candidates, audit reports, review
  records, bank entries, evaluation results.
- Postgres with pgvector: course material chunks, PYQ embeddings.
- Object store: source files (PDFs, Word docs), model snapshots.

---

## 7. Prototype scope (14–18 September)

### 7.1 What we demonstrate

One vertical slice of the platform on one course, end to end. The goal is to
show that the generation path from document to question is real and working,
even though breadth is narrow.

The demonstration is: document-to-question generation.

**Step-by-step user journey:**

1. Coordinator logs in.
2. Coordinator uploads one unit of course material as a PDF or Word file.
3. System parses the file, extracts page references, chunks and indexes the
   content. Screen shows chunk count and coverage so ingestion is visibly real.
4. Coordinator defines a small blueprint: two course outcomes, a few Bloom
   levels, a few mark weights. Saves it as a versioned record.
5. Faculty reviewer selects that blueprint and triggers generation.
6. System retrieves grounding passages from the uploaded document, drafts
   candidate questions through the LLM backend, and runs blueprint compliance
   checking.
7. Results screen shows each question with:
   - The citation and page number it was grounded from.
   - A compliance badge naming any violated blueprint field.
8. Reviewer accepts, edits and rejects questions.
9. Structured review records are written.
10. Accepted questions are published to the bank with provenance back to the
    generation run and blueprint version.

### 7.2 What is explicitly out of scope for this window

- **Novelty engine and PYQ comparison:** specified in the architecture but not
  demonstrated in this prototype. The ingestion path for PYQs may be built but
  the multi-signal scoring is not wired into the generation loop yet.
- **Ranker:** a stub or simple heuristic, not a fitted model. We will not have
  enough review records by the 18th to fit anything meaningful.
- **Baseline comparison and ablation runs:** described in the evaluation plan
  but not executed.
- **Self-hosted open-weight LLM path:** the adapter interface exists but only
  the hosted API adapter is wired for the prototype.
- **Chatbot module:** untouched. It remains secondary.
- **Multiple courses or blueprints:** the prototype runs on one course only.
- **Blinding and variance control:** these apply only to the evaluation
  comparison, which is not run in this window.

### 7.3 Why this scope is correct

Everything we are scoping out builds on top of this same pipeline rather than
requiring further architecture. The novelty engine hooks into the same
orchestrator after the compliance check. The ranker reads from the same review
record store. The self-hosted adapter implements the same interface. Widening to
more courses is a data and configuration matter, not a code matter. Proving the
vertical slice is real is more valuable at this stage than showing four
half-built engines.

---

## 8. Database schema (key tables, prototype)

```
courses
  course_id       UUID PK
  code            TEXT
  title           TEXT

materials
  material_id     UUID PK
  course_id       FK -> courses
  filename        TEXT
  source_type     TEXT (pdf, docx)
  stored_path     TEXT
  uploaded_at     TIMESTAMP

chunks
  chunk_id        UUID PK
  material_id     FK -> materials
  text            TEXT
  page_ref        TEXT
  embedding       VECTOR(384)

blueprints
  blueprint_id    UUID PK
  course_id       FK -> courses
  version         INT
  frozen_at       TIMESTAMP

blueprint_constraints
  constraint_id   UUID PK
  blueprint_id    FK -> blueprints
  course_outcome  TEXT
  concept         TEXT
  bloom_level     TEXT
  marks           INT
  question_type   TEXT
  target_difficulty TEXT

generation_runs
  run_id          UUID PK
  blueprint_id    FK -> blueprints
  backend_id      TEXT
  prompt_hash     TEXT
  temperature     FLOAT
  seed            INT
  started_at      TIMESTAMP
  status          TEXT

candidate_questions
  question_id     UUID PK
  run_id          FK -> generation_runs
  constraint_id   FK -> blueprint_constraints
  arm_label       TEXT NULL (null during normal use; baseline/ablation name
                            during evaluation)
  stem            TEXT
  answer_key      TEXT
  state           TEXT (drafted, compliant, violating, discarded, novel,
                        ranked, under_review, accepted, edited, rejected,
                        published, superseded)

grounding_citations
  citation_id     UUID PK
  question_id     FK -> candidate_questions
  chunk_id        FK -> chunks
  char_span       TEXT
  support_score   FLOAT

compliance_reports
  report_id       UUID PK
  question_id     FK -> candidate_questions
  is_compliant    BOOLEAN
  satisfaction_rate FLOAT

constraint_violations
  violation_id    UUID PK
  report_id       FK -> compliance_reports
  field           TEXT
  expected        TEXT
  observed        TEXT

review_records
  record_id       UUID PK
  question_id     FK -> candidate_questions
  reviewer_id     FK -> users
  decision        TEXT (accept, edit, reject)
  edited_stem     TEXT NULL
  edit_distance   FLOAT NULL
  reason          TEXT NULL
  reviewed_at     TIMESTAMP
  blinded         BOOLEAN DEFAULT false

question_bank
  entry_id        UUID PK
  question_id     FK -> candidate_questions
  version         INT
  snapshot        JSONB
  published_at    TIMESTAMP
```

Tables for novelty reports, PYQ metadata, ranker model versions, and evaluation
runs exist in the full schema but are not populated in the prototype.

Schema additions for the evaluation protocol:
- `generation_runs.temperature` and `generation_runs.seed` support variance
  control and reproducibility.
- `candidate_questions.arm_label` links each question to its baseline or
  ablation arm during evaluation runs; null during normal use.
- `review_records.blinded` marks whether the reviewer saw the arm label or not.

---

## 9. API endpoints (prototype)

```
POST   /auth/login                          -> OIDC redirect
GET    /auth/callback                       -> session token

POST   /courses/{id}/materials              -> upload and ingest
GET    /courses/{id}/materials              -> list with chunk counts

POST   /courses/{id}/blueprints             -> create draft
PUT    /blueprints/{id}/constraints         -> add/edit rows
POST   /blueprints/{id}/validate            -> check coverage and totals
POST   /blueprints/{id}/freeze              -> version and lock

POST   /blueprints/{id}/generate            -> start generation run
GET    /runs/{id}                           -> run status and candidates
GET    /runs/{id}/candidates                -> ranked list with audit evidence
GET    /candidates/{id}/evidence            -> citations and compliance detail

POST   /candidates/{id}/review              -> record accept/reject/edit
GET    /runs/{id}/reviews                   -> review summary

POST   /candidates/publish                  -> publish accepted to bank
GET    /bank/entries                        -> versioned bank listing
GET    /bank/entries/{id}                   -> entry with provenance
```

---

## 10. Prototype implementation plan (day by day)

### Day 1 (14 September): Ingestion and storage

- Set up Postgres with pgvector.
- Build the ingestion endpoint: accept PDF or Word file, parse with PyMuPDF or
  python-docx, chunk with sentence-window strategy, embed with
  sentence-transformers, store chunks and embeddings.
- Build the material listing endpoint that returns chunk counts.
- Verify: upload a real document, confirm chunks are indexed, confirm a
  similarity query returns relevant passages.

### Day 2 (15 September): Blueprint and generation

- Build blueprint CRUD: create draft, add constraints, validate coverage,
  freeze as version.
- Build the generation orchestrator: for each constraint, retrieve grounding
  passages via pgvector similarity search, build prompt with constraint and
  passages, call the hosted LLM API, parse the response into candidate
  question, answer key and citations.
- Log temperature and seed per generation call from day one so the evaluation
  protocol is supported from the start.
- Verify: define a blueprint, trigger generation, confirm candidates come back
  with citations attached.

### Day 3 (16 September): Blueprint compliance and results screen

- Build the blueprint compliance engine: check each candidate against its
  constraint row, produce compliance report with named violations.
- Wire compliance into the generation loop: after drafting, check compliance,
  and if violations are found and repair budget remains, regenerate with a
  corrected prompt.
- Build the results API endpoint that returns candidates with citations and
  compliance data.
- Start the React frontend: results screen showing question, citation with
  page number, and compliance badge.

### Day 4 (17 September): Review workflow and publication

- Build the review endpoints: accept, edit (with edit distance computation),
  reject with reason.
- Build the review record store writes.
- Build the publish endpoint: create versioned snapshot in the question bank
  with provenance links.
- Frontend: review controls (accept/edit/reject buttons), review summary,
  publish flow, bank listing with provenance.

### Day 5 (18 September): Integration, testing and demo rehearsal

- End-to-end test: upload document, define blueprint, generate, review,
  publish. The full journey without stopping.
- Fix integration bugs.
- Prepare the demo document and blueprint so the demonstration is rehearsed
  and does not depend on live debugging.
- Write a short script for the demo walkthrough so every team member knows
  what happens at each step.

---

## 11. What comes after the prototype

Once the vertical slice is working:

1. **Duplicate pair labelling (weeks 2-5, parallel):** start constructing hard
   negatives from real PYQs immediately. Paraphrase, renumber, reorder
   conditions. Label in batches. Target 300 pairs minimum (see Section 5 for
   the sample-size justification). Commit the train/val/test split file to the
   repository before any model training begins.
2. **Novelty engine (weeks 3-5):** ingest PYQs with metadata extraction, build
   the four-signal scorer with logistic regression combiner, wire it into the
   generation loop after compliance, add the novelty panel to the results
   screen. Tune threshold on validation fold only.
3. **Ranker (weeks 5-7):** once enough review records exist, feature-engineer
   from compliance scores, novelty signals and question attributes (no
   post-review features; see Section 3.1 leakage check). Fit LightGBM.
   Compare against logistic-regression floor and compliance-score-only floor.
   If LightGBM does not beat both, ship the simpler model.
4. **Self-hosted LLM path (week 6):** implement the second adapter, verify
   the same pipeline runs identically on the same seed.
5. **Blinded evaluation sessions (weeks 7-8):** prepare shuffled, arm-unlabelled
   question sets from all baselines and ablation arms. Run reviewer sessions
   with blinding enabled. Each reviewer rates the same question pool
   independently.
6. **Baseline and ablation runs (weeks 7-9):** run all four baselines and
   three ablation arms on the same frozen blueprint with the same reviewers.
   Three seeds per constraint per condition. Compute metrics with bootstrap
   confidence intervals.
7. **Evaluation dashboard (weeks 8-10):** compute and display all reported
   metrics, including NDCG@k for the ranker ablation.
8. **Failure analysis (week 10):** select qualitative examples (see
   Section 13.2) and write up the analysis.
9. **Report and final review (weeks 10-17):** write up, iterate, defend.

---

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Faculty reviewer availability | Blocks headline metric, ranker training, inter-rater agreement | Written commitment with dates now; short batched sessions; fast review UI; senior-student fallback on a subset |
| Too few labelled duplicate pairs | Cannot evaluate novelty engine; confidence intervals too wide | Start labelling week 1; construct hard negatives deliberately; 300 pair floor justified by interval width target |
| Hosted LLM API changes or goes down | Blocks generation | Adapter interface; freeze model version in config; self-hosted path working before final review |
| PYQ archive access delayed | Blocks novelty engine | Request sent now; build against small manual set in parallel |
| Chatbot module pulls effort | Core contribution incomplete | Explicitly secondary; untouched until pipeline is end to end |
| Reviewers disagree (low kappa) | Weakens acceptance metric | Review rubric before any reviewing; calibration session on shared questions; report agreement honestly with CI; if kappa below 0.4, flag the headline metric as weakly grounded |
| Ranker overfits on small data | Claimed adaptation is noise | Floor baselines (logistic regression, compliance-score-only); if LightGBM does not beat both, ship the simpler model and report that |
| Reviewer bias toward own system | Evaluation not credible | Blinded review sessions; questions shuffled across arms with no system label visible |
| Stochastic generation noise reported as real effect | False claims | Three runs per constraint per condition; report bootstrap CIs; no claim where CI on difference overlaps zero |

---

## 13. Success criteria

### 13.1 Metrics and targets

**Headline metric:** faculty acceptance without edits (proportion of
generated questions accepted as-is). Compared between the full system and
the fixed-prompt RAG baseline using McNemar's test on paired binary outcomes,
reported with a 95% bootstrap confidence interval on the difference. We do
not commit to a fixed point target (such as "15 percentage points") because
we have no prior study on this exact task to ground it against. Instead, we
commit to reporting the effect size and confidence interval, and claiming an
improvement only if the lower bound of the CI is above zero. If comparable
work in automated question generation is published before our evaluation
runs, we will adopt its reported acceptance rate as an anchoring baseline
and state a target relative to it.

**Supporting metrics (all reported with confidence intervals):**
- Duplicate-detection precision, recall, F1 on held-out test fold of labelled
  PYQ pair set. Bootstrap CIs.
- Blueprint constraint satisfaction rate. Wilson score interval.
- Grounding and citation correctness on a random sample of 50 citations.
  Wilson score interval.
- Mean normalised edit distance on edited questions, with standard deviation.
- Cohen's kappa between two reviewers, with bootstrap CI.
- NDCG@k for the ranker ablation, with bootstrap CI.
- Ablation results for each engine removed.

**Prototype-specific acceptance:** the end-to-end journey from document upload
to published question bank entry works on one course, with real course material,
and the compliance check visibly catches a violated constraint during the demo.

### 13.2 Failure modes and qualitative analysis

Aggregate metrics alone are insufficient for a research contribution. The
final report will include a qualitative failure analysis section containing
at minimum the following examples per engine:

**Novelty engine:**
- One true positive: a hard duplicate (paraphrased, renumbered) correctly
  caught, with the signal breakdown showing which signals fired.
- One false positive: a genuinely novel question incorrectly flagged as a
  duplicate, with analysis of which signal was misleading and why.
- One false negative: a real duplicate that the engine missed, with analysis
  of which signal failed and what would be needed to catch it.

**Blueprint compliance engine:**
- One true violation correctly caught (field, expected vs observed).
- One false alarm: a compliant question flagged as violating, with root cause
  (usually a Bloom-level misclassification).
- One missed violation that a reviewer caught manually.

**Ranker:**
- One case where the ranker correctly placed a reviewer-preferred question
  at the top of the list.
- One case where it ranked a rejected question above an accepted one, with
  the feature vector showing what misled the model.

These examples are selected after the evaluation runs, not cherry-picked
during development. Selection criteria: for true positives, choose the
hardest case that succeeded (closest to the decision boundary); for false
positives and negatives, choose the most instructive case (the one that
reveals the most about the engine's limitation). Each example is presented
with full context: the question text, the relevant PYQ or constraint, the
signal or feature values, and a two-sentence analysis.

This analysis is what separates a working system from a research
contribution, because it shows not just that the system works in aggregate
but where and why it fails, which is what a reviewer or a future builder
needs.
