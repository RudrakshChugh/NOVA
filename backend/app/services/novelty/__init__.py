"""Novelty engine (plan §3.1): score a candidate question against the past-year corpus on four
signals, combine them with a fitted logistic regression, and record the breakdown.

Entry points:
  build_corpus(db, course_id) -> Corpus     once per generation run
  similar_past_questions(corpus, vector)    what to tell the model *before* it drafts
  bank_references(db, course_id)            published questions a draft must not repeat either
  audit(db, question, corpus, internal)     -> NoveltyReport
"""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import settings
from ...models import (
    PYQ, CandidateQuestion, NoveltyReport, NoveltySignal, PYQPaper, QuestionBankEntry,
)
from ..embeddings import embed
from .combiner import NoveltyCombiner
from .signals import SIGNAL_NAMES, BM25Index, Metadata, signal_vector

def active_combiner_path(course_id: UUID) -> Path:
    """Where one subject’s fitted combiner lives.

    One file per subject, because a combiner is fitted on that subject’s past-year corpus and
    says nothing about another’s. Read through settings so a test run cannot overwrite the
    model the application is serving."""
    base = settings.novelty_model_path
    return base.with_name(f"{base.stem}_{course_id}{base.suffix}")


def load_combiner(course_id: UUID) -> NoveltyCombiner:
    """The fitted combiner if one has been trained for this subject, else the labelled
    untrained fallback."""
    path = active_combiner_path(course_id)
    return NoveltyCombiner.load(path) if path.exists() else NoveltyCombiner.untrained()


@dataclass
class Corpus:
    """The past-year questions of one course, plus the corpus statistics BM25 needs."""

    questions: list[PYQ]
    index: BM25Index
    combiner: NoveltyCombiner

    @property
    def is_empty(self) -> bool:
        return not self.questions


def build_corpus(db: Session, course_id: UUID) -> Corpus:
    questions = list(
        db.scalars(
            select(PYQ).join(PYQPaper, PYQPaper.paper_id == PYQ.paper_id).where(PYQPaper.course_id == course_id)
        ).all()
    )
    index = BM25Index({str(q.pyq_id): q.text for q in questions})
    return Corpus(questions=questions, index=index, combiner=load_combiner(course_id))


@dataclass
class Reference:
    """A question that is not from a past paper but that a new draft must still not repeat: an
    earlier candidate of the same run, or one already published to the bank.

    Past papers are not the only place a repeat can come from. A blueprint with two rows on one
    concept will happily produce the same question twice, and a second run will reproduce what
    the first one already put in the bank. Neither is caught by comparing with past papers alone.
    """

    text: str
    embedding: list[float]
    meta: Metadata
    source: str  # run | bank
    label: str  # "Q3 of this run", "question bank"


def similar_past_questions(
    corpus: "Corpus | None", query_vector: list[float], k: int, floor: float
) -> list[PYQ]:
    """The past-year questions closest to a blueprint row, for the model to steer away from.

    The audit only ever told the model about a past question *after* it had repeated one. Showing
    the nearest ones up front lets the first draft avoid them, which is cheaper than a redraft and
    does not spend the redraft budget. `floor` keeps unrelated questions out of the prompt: a
    list of things to avoid that has nothing to do with the row is noise the model then imitates.
    """
    if corpus is None or corpus.is_empty or k <= 0:
        return []
    scored = [
        (sum(a * b for a, b in zip(query_vector, q.embedding)), q) for q in corpus.questions
    ]
    scored.sort(key=lambda row: row[0], reverse=True)
    return [q for score, q in scored[:k] if score >= floor]


def bank_references(db: Session, course_id: UUID) -> list[Reference]:
    """Every question currently published for this subject, embedded once per run."""
    from ...models import Blueprint, BlueprintConstraint

    rows = db.execute(
        select(QuestionBankEntry, BlueprintConstraint)
        .join(CandidateQuestion, CandidateQuestion.question_id == QuestionBankEntry.question_id)
        .join(BlueprintConstraint, BlueprintConstraint.constraint_id == CandidateQuestion.constraint_id)
        .join(Blueprint, Blueprint.blueprint_id == BlueprintConstraint.blueprint_id)
        .where(Blueprint.course_id == course_id)
        .order_by(QuestionBankEntry.published_at)
    ).all()
    # A revised entry supersedes the one before it, so only the latest version of each question
    # is something a new draft could repeat.
    latest: dict = {}
    for entry, constraint in rows:
        stem = (entry.snapshot or {}).get("stem")
        if isinstance(stem, str) and stem.strip():
            latest[entry.question_id] = (stem.strip(), constraint)
    if not latest:
        return []
    texts = [stem for stem, _ in latest.values()]
    vectors = embed(texts)
    return [
        Reference(
            text=stem, embedding=vector, source="bank", label="question bank",
            meta=Metadata(question_type=c.question_type, marks=c.marks, bloom_level=c.bloom_level),
        )
        for (stem, c), vector in zip(latest.values(), vectors)
    ]


def score_against_references(
    stem: str, stem_vector: list[float], meta: Metadata, corpus: "Corpus",
    references: list[Reference], shortlist: int,
) -> list[tuple[Reference, dict[str, float], float]]:
    """The same four signals and the same combiner, applied to questions that are not past-year
    ones. One definition of "duplicate" everywhere, rather than a second, hand-set similarity
    cut-off for this case.

    BM25 needs corpus statistics, and the past-year index does not contain these texts, so a
    small index is built over both. It is rebuilt per call because the reference set grows by one
    with every candidate of the run; at the size of a question paper that costs nothing.
    """
    if not references:
        return []
    ranked = sorted(
        references, key=lambda r: sum(a * b for a, b in zip(stem_vector, r.embedding)), reverse=True
    )[:shortlist]
    documents = {str(q.pyq_id): q.text for q in corpus.questions}
    documents.update({f"ref:{i}": r.text for i, r in enumerate(references)})
    index = BM25Index(documents)
    out = []
    for ref in ranked:
        signals = signal_vector(
            left_text=stem, right_text=ref.text, left_vec=stem_vector, right_vec=list(ref.embedding),
            left_meta=meta, right_meta=ref.meta, index=index,
        )
        out.append((ref, signals, corpus.combiner.probability(signals)))
    out.sort(key=lambda row: row[2], reverse=True)
    return out


def _metadata_of(pyq: PYQ) -> Metadata:
    return Metadata(question_type=pyq.question_type, marks=pyq.marks, bloom_level=pyq.bloom_level)


def score_against_corpus(
    stem: str, stem_vector: list[float], meta: Metadata, corpus: Corpus, shortlist: int
) -> list[tuple[PYQ, dict[str, float], float]]:
    """Signals and duplicate probability for the most semantically similar past questions,
    highest probability first. The shortlist keeps the cost of the lexical and structural
    signals bounded as the corpus grows; it is ranked by the embedding, which is the signal
    that is cheap to compute for every row."""
    ranked = sorted(
        corpus.questions,
        key=lambda q: sum(a * b for a, b in zip(stem_vector, q.embedding)),
        reverse=True,
    )[:shortlist]
    out = []
    for pyq in ranked:
        signals = signal_vector(
            left_text=stem, right_text=pyq.text,
            left_vec=stem_vector, right_vec=list(pyq.embedding),
            left_meta=meta, right_meta=_metadata_of(pyq),
            index=corpus.index,
        )
        out.append((pyq, signals, corpus.combiner.probability(signals)))
    out.sort(key=lambda row: row[2], reverse=True)
    return out


def audit(
    db: Session, question: CandidateQuestion, corpus: Corpus,
    internal: list[Reference] | None = None,
) -> NoveltyReport:
    """Record a novelty report for one candidate draft. A report is written even when there is
    nothing to compare against, so a run always states what the novelty engine saw.

    `internal` is the set of non-past-paper questions the draft must not repeat. The report names
    whichever comparison scored highest, and says which kind it was, so a reviewer told "this is
    a duplicate" can see of what."""
    combiner = corpus.combiner
    constraint = question.constraint
    internal = internal or []
    meta = Metadata(
        question_type=constraint.question_type, marks=constraint.marks, bloom_level=constraint.bloom_level
    )

    # A redraft replaces the previous audit. Deleted through the ORM so the signal rows go with
    # the report; a bulk delete would leave them behind and trip the foreign key.
    for previous in db.scalars(
        select(NoveltyReport).where(NoveltyReport.question_id == question.question_id)
    ).all():
        db.delete(previous)
    db.flush()
    db.expire(question, ["novelty_reports"])

    if corpus.is_empty and not internal:
        report = NoveltyReport(
            question_id=question.question_id, nearest_pyq_id=None, duplicate_probability=0.0,
            threshold=combiner.threshold, is_duplicate=False,
            model_version=f"{combiner.version} (empty corpus)",
        )
        db.add(report)
        db.flush()
        return report

    stem_vector = embed([question.stem])[0]
    # (probability, signals, past question or None, internal reference or None). Past papers are
    # listed first so that on an exact tie the report names the past paper, as it always has.
    best: tuple[float, dict[str, float], PYQ | None, Reference | None] | None = None
    if not corpus.is_empty:
        pyq, signals, probability = score_against_corpus(
            question.stem, stem_vector, meta, corpus, settings.novelty_shortlist
        )[0]
        best = (probability, signals, pyq, None)
    scored_internal = score_against_references(
        question.stem, stem_vector, meta, corpus, internal, settings.novelty_shortlist
    )
    if scored_internal:
        ref, signals, probability = scored_internal[0]
        if best is None or probability > best[0]:
            best = (probability, signals, None, ref)

    probability, signals, nearest, ref = best
    report = NoveltyReport(
        question_id=question.question_id,
        nearest_pyq_id=nearest.pyq_id if nearest is not None else None,
        nearest_source="pyq" if nearest is not None else ref.source,
        nearest_text=nearest.text if nearest is not None else ref.text,
        nearest_label=None if nearest is not None else ref.label,
        duplicate_probability=probability, threshold=combiner.threshold,
        is_duplicate=probability >= combiner.threshold, model_version=combiner.version,
    )
    db.add(report)
    db.flush()
    for name in SIGNAL_NAMES:
        db.add(
            NoveltySignal(
                report_id=report.report_id, name=name, value=signals[name],
                weight=combiner.coefficients.get(name) if combiner.is_fitted else None,
            )
        )
    return report
