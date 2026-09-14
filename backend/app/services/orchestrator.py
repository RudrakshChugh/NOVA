"""Generation orchestrator (plan §6.2): for each blueprint constraint, retrieve grounding passages,
draft a candidate through the LLM adapter, run the compliance engine, and repair within budget.
Novelty scoring and the ranker hook in after the compliance step later (plan §7.3)."""

import hashlib
import random
from uuid import UUID

from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..llm import GenerationRequest, get_adapter
from ..models import (
    Blueprint,
    BlueprintConstraint,
    CandidateQuestion,
    Chunk,
    ComplianceReport,
    ConstraintViolation,
    GenerationRun,
    GroundingCitation,
)
from . import compliance, retrieval

SYSTEM_PROMPT = """You are an examination question setter. You write one exam question strictly from the
provided course passages. Every fact the question relies on must come from the passages. Cite the
passages you used by their number and quote the exact supporting sentence. Do not invent content
that is not in the passages."""

USER_TEMPLATE = """Blueprint constraint:
- course outcome: {course_outcome}
- concept: {concept}
- Bloom level: {bloom_level} (begin the question with a verb at this level)
- marks: {marks}
- question type: {question_type}
- target difficulty: {target_difficulty}

{type_rules}
{repair_block}
Course passages:
{passages}

Return the question, a complete answer key proportional to the marks, MCQ options only if the type is mcq
(exactly 4, and answer_key must be one of them verbatim), and citations (passage number + exact quote)."""

TYPE_RULES = {
    "mcq": "Type rules: exactly 4 options, one correct; answer_key repeats the correct option text verbatim.",
    "short_answer": "Type rules: no options; answer expected in a few sentences.",
    "long_answer": "Type rules: no options; answer expected as a structured multi-paragraph response.",
    "numerical": "Type rules: no options; the stem must give numeric data and the answer key must show the numeric result with working.",
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "stem": {"type": "string"},
        "answer_key": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"passage": {"type": "integer"}, "quote": {"type": "string"}},
                "required": ["passage", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["stem", "answer_key", "options", "citations"],
    "additionalProperties": False,
}


def prompt_hash() -> str:
    return hashlib.sha256((SYSTEM_PROMPT + USER_TEMPLATE).encode()).hexdigest()


def _build_user_prompt(c: BlueprintConstraint, passages: list[Chunk], violations: list[compliance.Violation]) -> str:
    repair_block = ""
    if violations:
        lines = "\n".join(f"- {v.field}: expected {v.expected}; previous draft had {v.observed}" for v in violations)
        repair_block = f"The previous draft violated the blueprint. Fix every item below:\n{lines}\n"
    rendered = "\n\n".join(f"[{i}] ({ch.page_ref}) {ch.text}" for i, ch in enumerate(passages, start=1))
    return USER_TEMPLATE.format(
        course_outcome=c.course_outcome,
        concept=c.concept,
        bloom_level=c.bloom_level,
        marks=c.marks,
        question_type=c.question_type,
        target_difficulty=c.target_difficulty,
        type_rules=TYPE_RULES[c.question_type],
        repair_block=repair_block,
        passages=rendered,
    )


def _attach_citations(db: Session, q: CandidateQuestion, passages: list[Chunk], raw: list[dict]) -> float | None:
    """Store citations; support_score is cosine similarity between the drafted question and the cited chunk.
    Returns the best support among citations whose quote is found verbatim in the cited chunk."""
    db.query(GroundingCitation).filter_by(question_id=q.question_id).delete()  # repairs replace citations
    db.expire(q, ["citations"])
    best: float | None = None
    body = f"{q.stem}\n{q.answer_key}"
    for cit in raw:
        idx = cit["passage"] - 1
        if not 0 <= idx < len(passages):
            continue
        chunk = passages[idx]
        quote = cit["quote"].strip()
        start = chunk.text.find(quote)
        span = f"{start}:{start + len(quote)}" if start >= 0 else "not_found"
        score = retrieval.similarity(body, chunk.text)
        db.add(GroundingCitation(question_id=q.question_id, chunk_id=chunk.chunk_id, char_span=span, support_score=score))
        if start >= 0:  # a quote that is not verbatim in the chunk does not count as grounding
            best = score if best is None else max(best, score)
    return best


def _record_report(db: Session, q: CandidateQuestion, rep: compliance.Report) -> None:
    report = ComplianceReport(question_id=q.question_id, is_compliant=rep.is_compliant, satisfaction_rate=rep.satisfaction_rate)
    db.add(report)
    db.flush()
    for v in rep.violations:
        db.add(ConstraintViolation(report_id=report.report_id, field=v.field, expected=v.expected, observed=v.observed))


def _generate_for_constraint(db: Session, run: GenerationRun, c: BlueprintConstraint, course_id: UUID) -> None:
    adapter = get_adapter()
    passages = [ch for ch, _ in retrieval.search(db, course_id, f"{c.concept}. {c.course_outcome}", settings.retrieval_top_k)]
    q = CandidateQuestion(run_id=run.run_id, constraint_id=c.constraint_id, stem="", answer_key="", state="drafted")
    db.add(q)
    db.flush()

    violations: list[compliance.Violation] = []
    for attempt in range(settings.repair_budget + 1):
        result = adapter.generate(
            GenerationRequest(
                system=SYSTEM_PROMPT,
                user=_build_user_prompt(c, passages, violations),
                json_schema=OUTPUT_SCHEMA,
                temperature=run.temperature,
                seed=run.seed,
            )
        )
        if attempt == 0:
            run.temperature, run.seed = result.temperature, result.seed  # what the backend actually honoured
        data = result.data
        q.stem, q.answer_key = data["stem"].strip(), data["answer_key"].strip()
        q.options = [o.strip() for o in data["options"]] or None
        q.repair_attempts = attempt
        best = _attach_citations(db, q, passages, data["citations"])
        rep = compliance.check(c, q.stem, q.answer_key, q.options, best)
        _record_report(db, q, rep)
        q.state = "compliant" if rep.is_compliant else "violating"
        db.commit()
        if rep.is_compliant:
            break
        violations = rep.violations


def execute_run(run_id: UUID) -> None:
    """Runs in a background task with its own session."""
    db = SessionLocal()
    try:
        run = db.get(GenerationRun, run_id)
        bp = db.get(Blueprint, run.blueprint_id)
        for c in bp.constraints:
            _generate_for_constraint(db, run, c, bp.course_id)
        run.status = "completed"
        db.commit()
    except Exception as e:  # surfaced on GET /runs/{id}
        db.rollback()
        run = db.get(GenerationRun, run_id)
        run.status, run.error = "failed", f"{type(e).__name__}: {e}"
        db.commit()
    finally:
        db.close()


def start_run(db: Session, bp: Blueprint) -> GenerationRun:
    run = GenerationRun(
        blueprint_id=bp.blueprint_id, backend_id=get_adapter().backend_id, prompt_hash=prompt_hash(), status="running",
        temperature=settings.generation_temperature, seed=random.SystemRandom().randrange(2**31),
    )
    db.add(run)
    db.commit()
    return run
