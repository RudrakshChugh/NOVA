"""Generation orchestrator (plan §6.2): for each blueprint constraint, retrieve grounding passages,
draft a candidate through the LLM adapter, run the compliance engine, and repair within budget.
Novelty scoring and the ranker hook in after the compliance step later (plan §7.3)."""

import hashlib
import random
from uuid import UUID

from sqlalchemy import select
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
    Material,
)
from . import compliance, novelty, retrieval
from .evaluation.arms import Arm, get as get_arm

SYSTEM_PROMPT = """You are an examination question setter. You write one exam question strictly from the
provided course passages. Every fact the question relies on must come from the passages. Cite the
passages you used by their number and quote the exact supporting sentence. Do not invent content
that is not in the passages."""

UNGROUNDED_SYSTEM_PROMPT = """You are an examination question setter. You write one exam question on the
topic you are given. Return an empty citation list, because no source passages have been provided."""

# Used by the arms that are told only the topic (plan §3.3.2): the model still has to know what to
# write about, but nothing about the Bloom level, marks or question type is stated.
TOPIC_TEMPLATE = """Topic:
- course outcome: {course_outcome}
- concept: {concept}

Write one exam question on this topic, with a complete answer key.
{passages_block}"""

USER_TEMPLATE = """Blueprint constraint:
- course outcome: {course_outcome}
- concept: {concept}
- Bloom level: {bloom_level} (begin the question with a verb at this level)
- marks: {marks}
- question type: {question_type}
- target difficulty: {target_difficulty}

{type_rules}
{repair_block}{novelty_block}
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
    """Fingerprint of every prompt template, so a run records exactly which prompts produced it."""
    joined = SYSTEM_PROMPT + UNGROUNDED_SYSTEM_PROMPT + USER_TEMPLATE + TOPIC_TEMPLATE
    return hashlib.sha256(joined.encode()).hexdigest()


def _render_passages(passages: list[Chunk]) -> str:
    return "\n\n".join(f"[{i}] ({ch.page_ref}) {ch.text}" for i, ch in enumerate(passages, start=1))


def _build_user_prompt(
    c: BlueprintConstraint,
    passages: list[Chunk],
    violations: list[compliance.Violation],
    duplicate_of: str | None = None,
    arm: Arm | None = None,
) -> str:
    """The prompt for one draft. Which blocks appear is the arm's doing, so a baseline differs
    from the full system only in what it is told, not in which model or templates it uses."""
    arm = arm or get_arm(None)
    if not arm.blueprint_in_prompt:
        block = f"Course passages:\n{_render_passages(passages)}" if passages else ""
        return TOPIC_TEMPLATE.format(
            course_outcome=c.course_outcome, concept=c.concept, passages_block=block
        )

    repair_block = ""
    if violations:
        lines = "\n".join(f"- {v.field}: expected {v.expected}; previous draft had {v.observed}" for v in violations)
        repair_block = f"The previous draft violated the blueprint. Fix every item below:\n{lines}\n"
    novelty_block = ""
    if duplicate_of:
        novelty_block = (
            "The previous draft repeats a question that has already been set in a past paper:\n"
            f'"{duplicate_of}"\n'
            "Write a different question for the same constraint. Changing only the wording or the "
            "numbers is not enough: ask about a different aspect of the concept.\n"
        )
    return USER_TEMPLATE.format(
        course_outcome=c.course_outcome,
        concept=c.concept,
        bloom_level=c.bloom_level,
        marks=c.marks,
        question_type=c.question_type,
        target_difficulty=c.target_difficulty,
        type_rules=TYPE_RULES[c.question_type],
        repair_block=repair_block,
        novelty_block=novelty_block,
        passages=_render_passages(passages),
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


def _passages_for(db: Session, c: BlueprintConstraint, course_id: UUID, arm: Arm) -> list[Chunk]:
    """What the model is grounded in under this arm: retrieved passages, the whole indexed
    material, or nothing at all."""
    if arm.full_material:
        return list(
            db.scalars(
                select(Chunk)
                .join(Material, Material.material_id == Chunk.material_id)
                .where(Material.course_id == course_id)
                .order_by(Chunk.page_ref)
                .limit(settings.source_grounded_max_chunks)
            ).all()
        )
    if arm.retrieval:
        query = f"{c.concept}. {c.course_outcome}"
        return [ch for ch, _ in retrieval.search(db, course_id, query, settings.retrieval_top_k)]
    return []


def _generate_for_constraint(
    db: Session,
    run: GenerationRun,
    c: BlueprintConstraint,
    course_id: UUID,
    corpus: novelty.Corpus | None,
    arm: Arm,
) -> None:
    """Draft, measure, and — where the arm enforces an audit — redraft with the failure named.

    Both audits always run and are always recorded, whatever the arm: an arm that does not enforce
    an audit still needs its constraint satisfaction rate and its duplicate rate measured, which is
    what the ablation comparison is made of. What the arm controls is whether a failure sends the
    draft back and whether it keeps the candidate from reaching a reviewer.
    """
    adapter = get_adapter()
    passages = _passages_for(db, c, course_id, arm)
    system = SYSTEM_PROMPT if passages else UNGROUNDED_SYSTEM_PROMPT
    q = CandidateQuestion(
        run_id=run.run_id, constraint_id=c.constraint_id, stem="", answer_key="",
        state="drafted", arm_label=run.arm_label,
    )
    db.add(q)
    db.flush()

    violations: list[compliance.Violation] = []
    duplicate_of: str | None = None
    repairs_used = novelty_redrafts_used = 0
    max_attempts = 1
    if arm.enforce_compliance:
        max_attempts += settings.repair_budget
    if arm.enforce_novelty and corpus is not None:
        max_attempts += settings.novelty_redraft_budget

    for attempt in range(max_attempts):
        result = adapter.generate(
            GenerationRequest(
                system=system,
                user=_build_user_prompt(c, passages, violations, duplicate_of, arm),
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
        violations, duplicate_of = rep.violations, None

        if arm.enforce_compliance and not rep.is_compliant:
            q.state = "violating"
            db.commit()
            if repairs_used < settings.repair_budget:
                repairs_used += 1
                continue
            break

        if corpus is None:
            q.state = "novel"
            db.commit()
            break

        report = novelty.audit(db, q, corpus)
        if arm.enforce_novelty and report.is_duplicate:
            q.state = "duplicate"
            db.commit()
            if novelty_redrafts_used < settings.novelty_redraft_budget:
                novelty_redrafts_used += 1
                duplicate_of = report.nearest_pyq.text if report.nearest_pyq else None
                violations = []
                continue
            break

        q.state = "novel"
        db.commit()
        break


def execute_run(run_id: UUID) -> None:
    """Runs in a background task with its own session."""
    db = SessionLocal()
    try:
        run = db.get(GenerationRun, run_id)
        arm = get_arm(run.arm_label)
        bp = db.get(Blueprint, run.blueprint_id)
        corpus = novelty.build_corpus(db, bp.course_id) if settings.novelty_enabled else None
        if corpus is not None and corpus.is_empty:
            corpus = None  # nothing to compare against; the run records this on each candidate
        for c in bp.constraints:
            _generate_for_constraint(db, run, c, bp.course_id, corpus, arm)
        run.status = "completed"
        db.commit()
    except Exception as e:  # surfaced on GET /runs/{id}
        db.rollback()
        run = db.get(GenerationRun, run_id)
        run.status, run.error = "failed", f"{type(e).__name__}: {e}"
        db.commit()
    finally:
        db.close()


def start_run(
    db: Session, bp: Blueprint, arm_label: str | None = None, seed: int | None = None
) -> GenerationRun:
    """`arm_label` is null for normal use and names the baseline or ablation during an evaluation.
    `seed` is supplied by the evaluation harness so a condition can be repeated under different
    seeds (plan §3.3.3); otherwise one is drawn and recorded."""
    run = GenerationRun(
        blueprint_id=bp.blueprint_id, backend_id=get_adapter().backend_id, prompt_hash=prompt_hash(),
        status="running", temperature=settings.generation_temperature,
        seed=seed if seed is not None else random.SystemRandom().randrange(2**31),
        arm_label=arm_label,
    )
    db.add(run)
    db.commit()
    return run
