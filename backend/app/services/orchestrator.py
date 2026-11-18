"""Generation orchestrator (plan §6.2): for each blueprint constraint, retrieve grounding passages,
draft a candidate through the LLM adapter, run the compliance engine, and repair within budget.
The novelty audit runs after compliance, and the ranker orders what survives (plan §7.3).

Three things here exist so that one bad reply cannot cost a whole run:

  * a reply is validated and coerced before anything reads it (`normalise_draft`);
  * a reply that cannot be read is asked for again, without spending the repair budget;
  * on a normal run, a row that still cannot be drafted is recorded and skipped, and the other
    rows are drafted anyway.
"""

import hashlib
import random
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..llm import GenerationRequest, MalformedOutput, adapter_for, get_adapter
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
from . import claims, compliance, novelty, retrieval
from .embeddings import embed
from .evaluation.arms import Arm, get as get_arm
from .novelty.signals import Metadata

SYSTEM_PROMPT = """You are an examination question setter. You write one exam question strictly from the
provided course passages. Every fact the question relies on must come from the passages. Cite the
passages you used by their number and quote the exact supporting sentence. Do not invent content
that is not in the passages.

The question and the answer key are printed on a paper and a marking scheme that nobody reads
alongside these passages. Never write "the passage", "passage 1", "(1)", "cite" or "as stated
above" in either of them: state the fact itself. Passage numbers belong only in the citations
list."""

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

{type_rules}
{bloom_rule}{length_rule}{plain_rule}{outcome_block}{originality_block}{facet_block}{suggestion_block}{repair_block}{novelty_block}{avoid_block}{sibling_block}
Course passages:
{passages}

Return the question, a complete answer key proportional to the marks, MCQ options only if the type is mcq
(exactly 4, and answer_key must be one of them verbatim), and citations (passage number + exact quote)."""

# Each of these states to the model a rule the audit will apply to its draft. Before they
# existed the model was told to "begin with a verb at this level" without being told which verbs
# count, and to make the answer "proportional to the marks" without the band it is measured
# against — so first drafts failed checks they had never been told about, and the repair budget
# went on telling them. The values are read from the compliance engine, never restated here, so
# the prompt and the check cannot drift apart.
BLOOM_RULE = (
    "Bloom rule: the first word of the question must be one of: {verbs}. The level is read from "
    "the earliest such verb in the question, so do not open with a verb from another level. "
    "That verb must be the task itself, not a lead-in to a description.\n"
)
LENGTH_RULE = "Answer-key length: between {low} and {high} words for {marks} mark(s).\n"
LENGTH_RULE_OPEN = "Answer-key length: at least {low} words for {marks} marks.\n"

OUTCOME_BLOCK = (
    "Course outcome being assessed:\n"
    "  {course_outcome}\n"
    "The marks are awarded for demonstrating this outcome through the concept above. Write the "
    "question so that answering it requires exactly that, and use the outcome's and the "
    "concept's own terms where they fit naturally.\n"
)

# What "novel" means differs by level. Recall and comprehension questions cannot be set in an
# invented scenario without inventing facts, which grounding forbids; from `apply` upwards a
# fresh situation is precisely what distinguishes a new question from a reworded old one.
ORIGINALITY_LOWER = (
    "Originality: phrase the question in your own words. Do not lift a sentence or a heading "
    "from the passages and turn it into the question.\n"
)
ORIGINALITY_HIGHER = (
    "Originality: do not reuse a worked example from the passages as the question. Set it in a "
    "new situation, case or data set that the facts in the passages are enough to answer.\n"
    # From real runs: every higher-level draft came back as "describe how you would ...,
    # outlining the key steps" — a recall question about a procedure, wearing an `analyze` verb.
    "Task: give the student the concrete material in the question and ask for the result "
    "itself — the elements of the diagram, the value, the comparison, the judgement. Do not "
    "ask them to \"describe how you would\", \"outline the steps\" or \"explain the process\" "
    "of doing it.\n"
)

AVOID_BLOCK = (
    "These questions on this topic have appeared in earlier papers. Do not repeat any of them, "
    "and do not ask the same thing with different wording or numbers — choose a different "
    "aspect of the concept:\n{items}\n"
)
# Seen on a real run: told only to "test something they do not", the model kept the task and
# swapped the setting — a university registration system became a library reservation system,
# and both questions asked for the same list of steps. Each scored well under the duplicate
# line, because they share little wording, so the audit could not catch it either. The task
# and the setting are therefore both named here as things that must differ.
SIBLING_BLOCK = (
    "These questions are already on this paper. Yours must test something they do not. Ask the "
    "student to do a different kind of task from theirs (for example construct, compare, find "
    "the error in, justify, or trace, rather than repeat the one they use), and do not reuse "
    "their scenario or setting:\n{items}\n"
)

# Also from a real run: the model wrote "(cite [1])" inside the answer key. Passage numbers
# mean nothing to a student or a marker, and they only exist in this prompt.
PLAIN_TEXT_RULE = (
    "Write the question and the answer key as they would be printed: no passage numbers, no "
    "\"cite\" markers and no first person. References to the passages go only in the "
    "citations list.\n"
)

# Two rows on one outcome, concept and level used to come back as one task in two settings: a
# bookstore, then a library, both "identify the entities and flows". Telling the model to differ
# did not stop it, and the audit could not see it. So the difference is no longer left to the
# model: each such row is assigned a different *form of task* before anything is drafted, and a
# different share of the retrieved passages (see `_passages_for`). The forms are fixed per Bloom
# level, and each one names a kind of result, so they are compatible with the level's cue verbs.
TASK_FORMS: dict[str, tuple[str, ...]] = {
    "remember": (
        "name the term or element that a given description fits",
        "state a definition or a rule exactly",
        "list the parts or components of something",
        "identify which of several given items belongs to a category",
    ),
    "understand": (
        "explain why something is done or needed",
        "describe the role one element plays in relation to the others",
        "illustrate a rule with one small example and say what it shows",
        "interpret what a given small artefact or statement means",
    ),
    "apply": (
        "construct the complete artefact for a given case",
        "complete a partly finished artefact that is given in the question",
        "determine a specific result or value from given data",
        "demonstrate a rule on one small given case",
    ),
    "analyze": (
        "find the errors in an artefact given in the question and say what each one violates",
        "compare two alternatives given in the question on stated criteria",
        "break a given case into its parts and state how each relates to the others",
        "trace what happens to one given input from start to finish",
    ),
    "evaluate": (
        "judge which of two given solutions is better for a stated purpose and justify it",
        "critique one given solution against a stated criterion",
        "assess whether a given artefact meets a stated requirement, with reasons",
        "recommend one of several given options and defend the choice",
    ),
    "create": (
        "design a solution for a stated need",
        "propose a modification to a design given in the question",
        "formulate a set of rules or elements for a stated case",
        "devise a plan that meets stated constraints",
    ),
}
FACET_BLOCK = (
    "Task form for this row: {form}. Another row of this paper covers the same concept at the "
    "same level in a different form, so keep strictly to this one.\n"
)

EVIDENCE_BLOCK = (
    "The answer key of the last draft states things about the subject that the passages do not "
    "say:\n{items}\n"
    "Write it again so that every rule, definition or property it relies on is stated in the "
    "passages. Keep what was supported. If the passages cannot support this question, ask a "
    "different one that they can.\n"
)

# Bumped whenever the wording above changes in a way the templates themselves do not capture.
PROMPT_VERSION = "4"

MAX_LISTED_CHARS = 280  # one listed question; a past paper's long question is trimmed, not dropped

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


def prompt_hash(suggestion: str | None = None) -> str:
    """Fingerprint of every prompt template **and of the run’s suggestion**, so a run records
    exactly which prompts produced it.

    The suggestion is part of the prompt, so leaving it out would let two runs that asked the
    model for different things share a hash and read as the same configuration."""
    joined = "".join((
        SYSTEM_PROMPT, UNGROUNDED_SYSTEM_PROMPT, USER_TEMPLATE, TOPIC_TEMPLATE, BLOOM_RULE,
        LENGTH_RULE, LENGTH_RULE_OPEN, OUTCOME_BLOCK, ORIGINALITY_LOWER, ORIGINALITY_HIGHER,
        AVOID_BLOCK, SIBLING_BLOCK, PLAIN_TEXT_RULE, FACET_BLOCK, EVIDENCE_BLOCK,
        repr(sorted(TASK_FORMS.items())), PROMPT_VERSION,
    ))
    return hashlib.sha256(f"{joined}\n--suggestion--\n{suggestion or ''}".encode()).hexdigest()


# "(cite [1])", "(see passage 2)", "[cite: 3]" — a prompt-internal reference that leaked into
# the text. Deliberately narrow: a bare "[1]" or "(1)" is left alone, since a numerical question
# or a numbered list can legitimately contain one.
_CITE_MARKER = re.compile(
    r"\s*[(\[]\s*(?:cite|see|source|ref(?:erence)?|from|per|as in)\b[^)\]]{0,40}?(?:passage\s*)?\[?\d+\]?[^)\]]{0,300}[)\]]"
    r"|\s*\(passage\s*\d{1,2}\)",
    re.IGNORECASE,
)


def normalise_draft(data: object) -> dict:
    """A model reply, checked and coerced into the shape everything downstream reads.

    Raises MalformedOutput when there is no question to read at all. Everything short of that
    is repaired rather than rejected: a null options list, a passage number sent as a string, a
    citation with no quote. Those say nothing about the question, and a draft should fail the
    audit for what is wrong with it, not crash the run for how it was serialised."""
    if not isinstance(data, dict):
        raise MalformedOutput(f"the model returned {type(data).__name__} where an object was expected")

    def text(key: str, numeric_ok: bool = False) -> str:
        value = data.get(key)
        if numeric_ok and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)  # a numerical answer key sometimes arrives as a bare number
        if not isinstance(value, str) or not value.strip():
            raise MalformedOutput(f"the model returned no usable `{key}`")
        return value.strip()

    stem, answer_key = text("stem"), text("answer_key", numeric_ok=True)
    stem, answer_key = _CITE_MARKER.sub("", stem).strip(), _CITE_MARKER.sub("", answer_key).strip()
    if not stem or not answer_key:
        raise MalformedOutput("the model returned only citation markers where text was expected")

    raw_options = data.get("options")
    options = [str(o).strip() for o in raw_options if str(o).strip()] if isinstance(raw_options, list) else []

    citations: list[dict] = []
    raw_citations = data.get("citations")
    for item in raw_citations if isinstance(raw_citations, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            passage = int(item.get("passage"))
        except (TypeError, ValueError):
            continue
        quote = item.get("quote")
        if isinstance(quote, str) and quote.strip():
            citations.append({"passage": passage, "quote": quote.strip()})
    return {"stem": stem, "answer_key": answer_key, "options": options, "citations": citations}


def row_seed(run_seed: int | None, position: int) -> int | None:
    """The seed for one row of a run.

    Every row used to be drafted with the run’s seed. Two rows with the same outcome, concept,
    level, marks and type then got an identical prompt *and* an identical seed, and a backend
    that honours the seed returned the same question twice. Offsetting by the row position keeps
    the run reproducible — the same run seed still gives the same paper — and keeps arms paired,
    since every arm of a study derives the same seed for the same row."""
    return None if run_seed is None else (run_seed + position) % (2**31)


def _trim(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_LISTED_CHARS else flat[: MAX_LISTED_CHARS - 1].rstrip() + "…"


def _listed(texts: list[str]) -> str:
    return "\n".join(f"- {_trim(t)}" for t in texts)


def bloom_rule(level: str) -> str:
    verbs = compliance.cue_verbs(level)
    return BLOOM_RULE.format(verbs=", ".join(v.capitalize() for v in verbs)) if verbs else ""


def length_rule(marks: int, question_type: str) -> str:
    """Empty for an MCQ, whose answer key is one option and is not length-checked."""
    if question_type == "mcq":
        return ""
    low, high = compliance.mark_band(marks)
    if high >= 10**5:
        return LENGTH_RULE_OPEN.format(low=low, marks=marks)
    return LENGTH_RULE.format(low=low, high=high, marks=marks)


def originality_block(level: str) -> str:
    higher = compliance.bloom_rank(level) >= compliance.bloom_rank("apply")
    return ORIGINALITY_HIGHER if higher else ORIGINALITY_LOWER


def twin_groups(constraints) -> dict:
    """For every row that shares its outcome, concept and Bloom level with another:
    constraint_id -> (its index within that group, the size of the group). Rows with no twin
    are absent, so a blueprint without any behaves exactly as before."""
    groups: dict[tuple, list] = {}
    for c in constraints:
        key = (c.outcome_id or c.course_outcome.strip().lower(), c.concept.strip().lower(), c.bloom_level)
        groups.setdefault(key, []).append(c)
    return {
        c.constraint_id: (i, len(members))
        for members in groups.values() if len(members) > 1
        for i, c in enumerate(members)
    }


def facet_block(level: str, twin: tuple[int, int] | None) -> str:
    forms = TASK_FORMS.get(level)
    if not twin or not forms:
        return ""
    return FACET_BLOCK.format(form=forms[twin[0] % len(forms)])


def _render_passages(passages: list[Chunk]) -> str:
    return "\n\n".join(f"[{i}] ({ch.page_ref}) {ch.text}" for i, ch in enumerate(passages, start=1))


def _build_user_prompt(
    c: BlueprintConstraint,
    passages: list[Chunk],
    violations: list[compliance.Violation],
    duplicate_of: str | None = None,
    arm: Arm | None = None,
    suggestion: str | None = None,
    avoid: list[str] | None = None,
    siblings: list[str] | None = None,
    duplicate_source: str = "pyq",
    twin: tuple[int, int] | None = None,
    unsupported: list[str] | None = None,
) -> str:
    """The prompt for one draft. Which blocks appear is the arm's doing, so a baseline differs
    from the full system only in what it is told, not in which model or templates it uses."""
    arm = arm or get_arm(None)
    if not arm.blueprint_in_prompt:
        block = f"Course passages:\n{_render_passages(passages)}" if passages else ""
        return TOPIC_TEMPLATE.format(
            course_outcome=c.course_outcome, concept=c.concept, passages_block=block
        )

    # Delimited and labelled as a preference, never as a rule. The blueprint row above is what
    # the draft is judged against; a suggestion that contradicts it produces a violation rather
    # than a quiet override, which is also what makes pasted text safe to put here.
    suggestion_block = ""
    if suggestion and suggestion.strip():
        suggestion_block = (
            "The reviewer has asked for this, in their own words. Follow it where it does not\n"
            "conflict with the blueprint constraint above, which always wins:\n"
            f"<<<\n{suggestion.strip()}\n>>>\n"
        )
    repair_block = ""
    if violations:
        lines = "\n".join(f"- {v.field}: expected {v.expected}; previous draft had {v.observed}" for v in violations)
        repair_block = f"The previous draft violated the blueprint. Fix every item below:\n{lines}\n"
    novelty_block = ""
    if duplicate_of:
        where = {
            "run": "is already on this paper",
            "bank": "is already in the question bank",
        }.get(duplicate_source, "has already been set in a past paper")
        novelty_block = (
            f"The previous draft repeats a question that {where}:\n"
            f'"{duplicate_of}"\n'
            "Write a different question for the same constraint. Changing only the wording or the "
            "numbers is not enough: ask about a different aspect of the concept.\n"
        )
    # Told up front rather than discovered by the audit. Only arms that enforce novelty get
    # these: an arm that is not supposed to know about past papers must not be shown them.
    avoid_block = AVOID_BLOCK.format(items=_listed(avoid)) if avoid and arm.enforce_novelty else ""
    sibling_block = SIBLING_BLOCK.format(items=_listed(siblings)) if siblings and arm.enforce_novelty else ""
    if unsupported:
        repair_block += EVIDENCE_BLOCK.format(items=_listed(unsupported))
    return USER_TEMPLATE.format(
        facet_block=facet_block(c.bloom_level, twin) if arm.enforce_novelty else "",
        bloom_rule=bloom_rule(c.bloom_level),
        plain_rule=PLAIN_TEXT_RULE,
        length_rule=length_rule(c.marks, c.question_type),
        outcome_block=OUTCOME_BLOCK.format(course_outcome=c.course_outcome),
        originality_block=originality_block(c.bloom_level),
        avoid_block=avoid_block,
        sibling_block=sibling_block,
        course_outcome=c.course_outcome,
        concept=c.concept,
        bloom_level=c.bloom_level,
        marks=c.marks,
        question_type=c.question_type,
        type_rules=TYPE_RULES[c.question_type],
        suggestion_block=suggestion_block,
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
    for cit in raw:  # already normalised: an int passage number and a non-empty quote
        idx = cit["passage"] - 1
        if not 0 <= idx < len(passages):
            continue
        chunk = passages[idx]
        quote = cit["quote"]
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


def _passages_for(
    db: Session, c: BlueprintConstraint, course_id: UUID, arm: Arm, twin: tuple[int, int] | None = None,
) -> list[Chunk]:
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
        k = settings.retrieval_top_k
        if not twin:
            return [ch for ch, _ in retrieval.search(db, course_id, query, k)]
        # Twin rows have the same query, so the same search would hand both the same five
        # passages — and the same passages are half of why they produced the same question.
        # A wider search is dealt out in turn: with two twins, one row gets the 1st, 3rd, 5th ...
        # best passage and the other the 2nd, 4th, 6th. Every share is still made of the
        # passages closest to the concept; if the material is too small to fill one, it is
        # topped up from the best that remain.
        index, size = twin
        ranked = [ch for ch, _ in retrieval.search(db, course_id, query, k * size)]
        share = ranked[index::size][:k]
        for chunk in ranked:
            if len(share) >= k:
                break
            if chunk not in share:
                share.append(chunk)
        return share
    return []


class UncoveredConcept(RuntimeError):
    """The indexed material has nothing close enough to a row's concept to write from."""


def coverage(db: Session, course_id: UUID, c) -> float | None:
    """How well the material covers one blueprint row: the similarity of the best passage
    retrieval finds for it, or None when nothing is indexed at all."""
    found = retrieval.search(db, course_id, f"{c.concept}. {c.course_outcome}", 1)
    return found[0][1] if found else None


def is_uncovered(score: float | None) -> bool:
    floor = settings.retrieval_min_similarity
    return floor > 0 and score is not None and score < floor


def _draft(adapter, request: GenerationRequest):
    """One readable reply. A reply that cannot be read is asked for again, up to the budget,
    because it is a bad sample rather than a bad question and says nothing about the row."""
    last: MalformedOutput | None = None
    for _ in range(settings.malformed_retry_budget + 1):
        try:
            result = adapter.generate(request)
            return result, normalise_draft(result.data)
        except MalformedOutput as e:
            last = e
    raise MalformedOutput(f"{last} (after {settings.malformed_retry_budget + 1} attempts)")


def _related_siblings(c: BlueprintConstraint, drafted: list[tuple[BlueprintConstraint, str]]) -> list[str]:
    """Stems already drafted in this run for the same outcome or the same concept. Those are the
    rows a new draft is most likely to collide with; listing the whole paper would only bury them."""
    related = [
        stem for other, stem in drafted
        if other.concept.strip().lower() == c.concept.strip().lower()
        or (other.outcome_id is not None and other.outcome_id == c.outcome_id)
        or other.course_outcome == c.course_outcome
    ]
    return related[-settings.sibling_prompt_limit:] if settings.sibling_prompt_limit > 0 else []


def _generate_for_constraint(
    db: Session,
    run: GenerationRun,
    c: BlueprintConstraint,
    course_id: UUID,
    corpus: novelty.Corpus | None,
    arm: Arm,
    base_seed: int | None = None,
    drafted: list[tuple[BlueprintConstraint, str]] | None = None,
    internal: list[novelty.Reference] | None = None,
    require_coverage: bool = False,
    twin: tuple[int, int] | None = None,
) -> CandidateQuestion:
    """Draft, measure, and — where the arm enforces an audit — redraft with the failure named.

    Both audits always run and are always recorded, whatever the arm: an arm that does not enforce
    an audit still needs its constraint satisfaction rate and its duplicate rate measured, which is
    what the ablation comparison is made of. What the arm controls is whether a failure sends the
    draft back and whether it keeps the candidate from reaching a reviewer.

    `drafted` and `internal` are this run's earlier questions: the first is shown to the model so
    it can steer clear, the second is what the audit then checks the draft against.
    """
    # Pinned to whatever this run committed to at start, never re-probed mid-run.
    adapter = adapter_for(run.backend_id) or get_adapter()
    if require_coverage and arm.retrieval:
        # Decided before a single token is drafted. A model given five unrelated passages and
        # told to stay within them does not decline; it writes a fluent question whose facts
        # came from nowhere, cites a passage, and passes. Not drafting is the honest result.
        best = coverage(db, course_id, c)
        if is_uncovered(best):
            raise UncoveredConcept(
                f"the course material has nothing close enough to \u201c{c.concept}\u201d to write from "
                f"(best match {best:.2f}, needs {settings.retrieval_min_similarity:.2f}). "
                "Upload material that covers it, or change the row."
            )
    passages = _passages_for(db, c, course_id, arm, twin)
    system = SYSTEM_PROMPT if passages else UNGROUNDED_SYSTEM_PROMPT
    internal = internal or []
    has_past_papers = corpus is not None and not corpus.is_empty
    audited = corpus is not None and (has_past_papers or bool(internal))

    avoid: list[str] = []
    if has_past_papers and arm.enforce_novelty and arm.blueprint_in_prompt:
        query_vector = embed([f"{c.concept}. {c.course_outcome}"])[0]
        avoid = [
            q.text for q in novelty.similar_past_questions(
                corpus, query_vector, settings.novelty_avoid_k, settings.novelty_avoid_min_similarity
            )
        ]
    siblings = _related_siblings(c, drafted or [])

    q = CandidateQuestion(
        run_id=run.run_id, constraint_id=c.constraint_id, stem="", answer_key="",
        state="drafted", arm_label=run.arm_label,
    )
    db.add(q)
    db.flush()

    violations: list[compliance.Violation] = []
    duplicate_of: str | None = None
    duplicate_source = "pyq"
    repairs_used = novelty_redrafts_used = 0
    max_attempts = 1
    if arm.enforce_compliance:
        max_attempts += settings.repair_budget
    if arm.enforce_novelty and audited:
        max_attempts += settings.novelty_redraft_budget
    seed = row_seed(base_seed, c.position)

    # Evidence for the reviewer, gathered on a draft that has passed both audits. Normal runs
    # only: it is an extra model call, and an evaluation arm must make exactly the calls its
    # definition says. Not for an MCQ: its answer key is one option, and "which of these is NOT
    # listed" has by construction no sentence of the material behind it.
    check_evidence = (
        require_coverage and settings.claim_check_enabled and bool(passages) and c.question_type != "mcq"
    )
    if check_evidence:
        max_attempts += settings.evidence_redraft_budget
    evidence_redrafts_used = 0
    unsupported: list[str] = []

    def _evidence_redraft() -> bool:
        """Store the evidence report for the current draft; True when the draft should be
        written again because its answer key says things the passages do not.

        The first real run that had this check showed why it has to act and not only report:
        an answer key rested on two rules of data flow diagrams that are true, and that the
        slides it was given never state. A marker working from those slides could not have
        awarded the marks."""
        nonlocal evidence_redrafts_used, unsupported, violations
        if not check_evidence:
            db.commit()
            return False
        report = claims.check(adapter, q.stem, q.answer_key, passages)
        q.claim_check = report
        db.commit()
        missing = [x["text"] for x in (report or {}).get("claims", []) if x["kind"] == "material" and not x["verified"]]
        if missing and evidence_redrafts_used < settings.evidence_redraft_budget:
            evidence_redrafts_used += 1
            unsupported, violations = missing, []
            return True
        return False

    for attempt in range(max_attempts):
        result, data = _draft(
            adapter,
            GenerationRequest(
                system=system,
                user=_build_user_prompt(
                    c, passages, violations, duplicate_of, arm, run.suggestion,
                    avoid=avoid, siblings=siblings, duplicate_source=duplicate_source, twin=twin,
                    unsupported=unsupported,
                ),
                json_schema=OUTPUT_SCHEMA,
                temperature=run.temperature,
                seed=seed,
            ),
        )
        if attempt == 0:
            # What the backend actually honoured. The run keeps its own seed, not this row's
            # offset one: recording the offset would shift every later row by it again.
            run.temperature = result.temperature
            run.seed = base_seed if result.seed is not None else None
        q.stem, q.answer_key = data["stem"], data["answer_key"]
        q.options = data["options"] or None
        q.repair_attempts = attempt
        best = _attach_citations(db, q, passages, data["citations"])
        rep = compliance.check(c, q.stem, q.answer_key, q.options, best)
        _record_report(db, q, rep)
        violations, duplicate_of, unsupported = rep.violations, None, []

        if arm.enforce_compliance and not rep.is_compliant:
            q.state = "violating"
            db.commit()
            if repairs_used < settings.repair_budget:
                repairs_used += 1
                continue
            break

        if not audited:
            q.state = "novel"
            if _evidence_redraft():
                continue
            break

        report = novelty.audit(db, q, corpus, internal)
        if arm.enforce_novelty and report.is_duplicate:
            q.state = "duplicate"
            db.commit()
            if novelty_redrafts_used < settings.novelty_redraft_budget:
                novelty_redrafts_used += 1
                duplicate_of = report.nearest_text or (report.nearest_pyq.text if report.nearest_pyq else None)
                duplicate_source = report.nearest_source or "pyq"
                violations = []
                continue
            break

        q.state = "novel"
        if _evidence_redraft():
            continue
        break
    return q


def _discard_blank(db: Session, run_id: UUID, constraint_id: UUID) -> None:
    """Remove a candidate that never received a draft. Left in place it would sit in the review
    queue as an empty question."""
    for blank in db.scalars(
        select(CandidateQuestion).where(
            CandidateQuestion.run_id == run_id, CandidateQuestion.constraint_id == constraint_id,
            CandidateQuestion.stem == "",
        )
    ).all():
        db.delete(blank)
    db.commit()


def execute_run(run_id: UUID) -> None:
    """Runs in a background task with its own session."""
    db = SessionLocal()
    try:
        run = db.get(GenerationRun, run_id)
        if run is None:  # deleted between being queued and being picked up
            return
        arm = get_arm(run.arm_label)
        bp = db.get(Blueprint, run.blueprint_id)
        # Kept even when no past paper exists: the audit also compares a draft with this run's
        # earlier questions and with the bank, and it needs the combiner to do that.
        corpus = novelty.build_corpus(db, bp.course_id) if settings.novelty_enabled else None

        # An evaluation run is measured on its duplicate rate against past papers, and that
        # definition must not move, so the wider comparison is for normal use only. For the
        # same reason a study must have every row: a normal run skips a row it cannot draft,
        # an evaluation run fails.
        normal_use = run.arm_label is None
        widen = corpus is not None and normal_use and settings.novelty_internal_check
        internal: list[novelty.Reference] = novelty.bank_references(db, bp.course_id) if widen else []
        drafted: list[tuple[BlueprintConstraint, str]] = []
        base_seed = run.seed
        failures: list[str] = []
        twins = twin_groups(bp.constraints)

        for c in bp.constraints:
            try:
                q = _generate_for_constraint(
                    db, run, c, bp.course_id, corpus, arm,
                    base_seed=base_seed, drafted=drafted, internal=internal,
                    require_coverage=normal_use, twin=twins.get(c.constraint_id),
                )
            except (MalformedOutput, UncoveredConcept) as e:
                db.rollback()
                _discard_blank(db, run_id, c.constraint_id)
                if not normal_use:
                    raise
                failures.append(f"Q{c.position + 1}: {e}")
                continue
            drafted.append((c, q.stem))
            if widen:
                internal.append(novelty.Reference(
                    text=q.stem, embedding=embed([q.stem])[0], source="run",
                    label=f"Q{c.position + 1} of this run",
                    meta=Metadata(question_type=c.question_type, marks=c.marks, bloom_level=c.bloom_level),
                ))

        total = len(bp.constraints)
        if failures and len(failures) == total:
            run.status = "failed"
            run.error = "No row could be drafted. " + " | ".join(failures)
        else:
            run.status = "completed"
            if failures:
                run.error = (
                    f"{len(failures)} of {total} row(s) could not be drafted and were skipped. "
                    + " | ".join(failures)
                )
        db.commit()
    except Exception as e:  # surfaced on GET /runs/{id}
        db.rollback()
        run = db.get(GenerationRun, run_id)
        if run is not None:
            # A row that was mid-draft when the backend went away has no question in it.
            for blank in db.scalars(
                select(CandidateQuestion).where(
                    CandidateQuestion.run_id == run_id, CandidateQuestion.stem == ""
                )
            ).all():
                db.delete(blank)
            run.status, run.error = "failed", f"{type(e).__name__}: {e}"
            db.commit()
    finally:
        db.close()


def recover_interrupted_runs() -> int:
    """Fail the runs a restart left behind.

    A run executes in a background task of the server process. If that process stops — a deploy,
    a crash, a laptop lid — nothing is left to finish it, yet its row still says `running`, and
    the interface refuses to start another while one is in progress. Called once at startup,
    when by definition no run of this process can be under way."""
    with SessionLocal() as db:
        stuck = list(db.scalars(select(GenerationRun).where(GenerationRun.status == "running")).all())
        for run in stuck:
            for blank in db.scalars(
                select(CandidateQuestion).where(
                    CandidateQuestion.run_id == run.run_id, CandidateQuestion.stem == ""
                )
            ).all():
                db.delete(blank)
            run.status = "failed"
            run.error = (
                "Interrupted: the server restarted while this run was in progress. "
                "The questions drafted before that are kept; start a new run for the rest."
            )
        db.commit()
        return len(stuck)


def start_run(
    db: Session, bp: Blueprint, arm_label: str | None = None, seed: int | None = None,
    suggestion: str | None = None,
) -> GenerationRun:
    """`arm_label` is null for normal use and names the baseline or ablation during an evaluation.
    `seed` is supplied by the evaluation harness so a condition can be repeated under different
    seeds (plan §3.3.3); otherwise one is drawn and recorded."""
    suggestion = (suggestion or "").strip() or None
    run = GenerationRun(
        blueprint_id=bp.blueprint_id, backend_id=get_adapter().backend_id,
        prompt_hash=prompt_hash(suggestion),
        status="running", temperature=settings.generation_temperature,
        seed=seed if seed is not None else random.SystemRandom().randrange(2**31),
        arm_label=arm_label, suggestion=suggestion,
    )
    db.add(run)
    db.commit()
    return run
