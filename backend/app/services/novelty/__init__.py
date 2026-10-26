"""Novelty engine (plan §3.1): score a candidate question against the past-year corpus on four
signals, combine them with a fitted logistic regression, and record the breakdown.

Entry points:
  build_corpus(db, course_id) -> Corpus     once per generation run
  audit(db, question, corpus) -> NoveltyReport
"""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import settings
from ...models import PYQ, CandidateQuestion, NoveltyReport, NoveltySignal, PYQPaper
from ..embeddings import embed
from .combiner import NoveltyCombiner
from .signals import SIGNAL_NAMES, BM25Index, Metadata, signal_vector

def active_combiner_path() -> Path:
    """Where the fitted combiner lives. Read through settings so a test run cannot overwrite the
    model the application is serving."""
    return settings.novelty_model_path


def load_combiner() -> NoveltyCombiner:
    """The fitted combiner if one has been trained, else the labelled untrained fallback."""
    path = active_combiner_path()
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
    return Corpus(questions=questions, index=index, combiner=load_combiner())


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


def audit(db: Session, question: CandidateQuestion, corpus: Corpus) -> NoveltyReport:
    """Record a novelty report for one candidate draft. A report is written even when the corpus
    is empty, so a run always states what the novelty engine saw."""
    combiner = corpus.combiner
    constraint = question.constraint
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

    if corpus.is_empty:
        report = NoveltyReport(
            question_id=question.question_id, nearest_pyq_id=None, duplicate_probability=0.0,
            threshold=combiner.threshold, is_duplicate=False,
            model_version=f"{combiner.version} (empty corpus)",
        )
        db.add(report)
        db.flush()
        return report

    stem_vector = embed([question.stem])[0]
    scored = score_against_corpus(question.stem, stem_vector, meta, corpus, settings.novelty_shortlist)
    nearest, signals, probability = scored[0]

    report = NoveltyReport(
        question_id=question.question_id, nearest_pyq_id=nearest.pyq_id,
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
