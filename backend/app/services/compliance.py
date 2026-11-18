"""Blueprint compliance engine (plan §3.1): deterministic, rule-based checks of a candidate
against its constraint row. Never asks the LLM to grade itself.

Checks (one per blueprint field it can observe from the candidate text):
  bloom_level    keyword/structure Bloom classifier on the stem vs constraint
  question_type  structural check (options present/absent, numeric content)
  marks          answer-key length band as the proxy for mark weighting
  concept        constraint concept content words appear in stem or answer
  course_outcome outcome content words appear in stem or answer
  grounding      at least one citation whose quote is verbatim in the cited chunk and clears the support floor
Every field of a constraint row is checked here. A field with no deterministic rule on the
candidate text does not belong on the row at all, which is why target_difficulty was removed
(plan.md 14.7) rather than being checked somewhere softer.
"""

import re
from dataclasses import dataclass

from ..config import settings

# Bloom cue verbs, ordered from lowest to highest level. The classifier picks the level of the
# earliest cue in the stem (the leading imperative); ties at the same position go to the higher level.
BLOOM_CUES: dict[str, tuple[str, ...]] = {
    "remember": ("define", "list", "name", "state", "recall", "identify", "label", "recognize", "what is", "who", "when"),
    "understand": ("explain", "describe", "summarize", "summarise", "interpret", "classify", "discuss", "outline", "paraphrase", "illustrate", "why"),
    "apply": ("apply", "calculate", "compute", "solve", "demonstrate", "use", "implement", "determine", "find", "show how", "execute"),
    "analyze": ("analyze", "analyse", "compare", "contrast", "differentiate", "distinguish", "examine", "break down", "categorize", "trace", "derive"),
    "evaluate": ("evaluate", "justify", "critique", "assess", "judge", "argue", "defend", "recommend", "which is better", "to what extent"),
    "create": ("design", "construct", "develop", "formulate", "propose", "compose", "devise", "plan", "build", "invent"),
}
_LEVEL_RANK = {lvl: i for i, lvl in enumerate(BLOOM_CUES)}

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "their", "using", "able", "will",
    "about", "over", "between", "under", "students", "student", "should", "course", "unit",
}

# answer-key word-count band per mark weighting: (min_marks, max_marks, min_words, max_words)
MARK_BANDS: tuple[tuple[int, int, int, int], ...] = (
    (1, 2, 1, 80),
    (3, 5, 20, 250),
    (6, 10, 60, 600),
)
_ABOVE_BAND = (200, 10**6)


@dataclass
class Violation:
    field: str
    expected: str
    observed: str


@dataclass
class Report:
    is_compliant: bool
    satisfaction_rate: float
    violations: list[Violation]


def classify_bloom(stem: str) -> str | None:
    text = stem.lower()
    hits: list[tuple[int, str]] = []
    for level, cues in BLOOM_CUES.items():
        for cue in cues:
            m = re.search(rf"\b{re.escape(cue)}\b", text)
            if m:
                hits.append((m.start(), level))
    if not hits:
        return None
    hits.sort(key=lambda h: (h[0], -_LEVEL_RANK[h[1]]))
    return hits[0][1]


# A higher-level verb in front of a request to describe a procedure. Seen on every early real
# run: "Analyze the scenario ... describe how you would ..., outlining the key steps". The
# opening verb says `analyze`; what the student is actually asked to do is recall a method,
# which is `understand`. Reading only the first verb let all of these through.
_PROCEDURE_ASK = re.compile(
    r"\b(?:describe|explain|outline|list|state|discuss|summari[sz]e|mention)\b[^.?!]{0,40}?"
    r"\b(?:how (?:you|one|they) (?:would|will|can|could)|the (?:key |main |major |necessary |basic )?steps"
    r"|the (?:process|procedure|approach|method) (?:of|for|to|you|used))\b"
    r"|\b(?:outlining|listing|describing|explaining) the (?:key |main |major )?steps\b"
    r"|\bsteps (?:you|one) would (?:take|follow)\b"
    r"|\bhow would you (?:go about|approach)\b",
    re.IGNORECASE,
)


def procedure_ask(stem: str) -> str | None:
    """The phrase, if the stem asks for a description of how something is done rather than for
    the thing itself."""
    match = _PROCEDURE_ASK.search(stem)
    return match.group(0).strip() if match else None


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9\-]{3,}", text.lower()) if w not in _STOPWORDS}


def _overlap(needle: str, haystack: str) -> list[str]:
    words = _content_words(needle)
    hay = haystack.lower()
    return sorted(w for w in words if w[:5] in hay)


def _mark_band(marks: int) -> tuple[int, int]:
    for lo, hi, min_words, max_words in MARK_BANDS:
        if lo <= marks <= hi:
            return (min_words, max_words)
    return _ABOVE_BAND


def mark_band(marks: int) -> tuple[int, int]:
    """The answer-key word band a mark weighting is checked against. Public so the generation
    prompt can state the same numbers the check enforces."""
    return _mark_band(marks)


def cue_verbs(level: str) -> tuple[str, ...]:
    """Single-word cue verbs for a Bloom level, for the prompt. Multi-word cues ("what is",
    "show how") are left out: they are recognised in a stem, but are not something to tell a
    model to open a question with."""
    return tuple(c for c in BLOOM_CUES.get(level, ()) if " " not in c and c not in ("who", "when", "why"))


def outcome_bloom_level(outcome_text: str) -> str | None:
    """The Bloom level a course outcome is itself stated at, read from its leading verb.

    "CO3 Design a normalised schema ..." is stated at `create`. The outcome code is dropped
    first so that it cannot be mistaken for a word. Returns None when the statement opens with
    no recognised cue, which is common and means only that nothing can be said."""
    text = re.sub(r"^\s*C[O0]\s*[-.]?\s*\d{1,2}\s*[-:.)]?\s*", "", outcome_text, flags=re.IGNORECASE)
    return classify_bloom(text)


def bloom_rank(level: str) -> int:
    return _LEVEL_RANK[level]


def checked_fields(constraint) -> list[str]:
    """Fields the engine checks for this constraint, in report order."""
    fields = ["bloom_level", "question_type"]
    if constraint.question_type != "mcq":
        fields.append("marks")
    return fields + ["concept", "course_outcome", "grounding"]


def check(constraint, stem: str, answer_key: str, options: list[str] | None, best_support: float | None) -> Report:
    violations: list[Violation] = []
    body = f"{stem}\n{answer_key}"
    checks = 0

    # bloom_level
    checks += 1
    observed_bloom = classify_bloom(stem)
    if observed_bloom != constraint.bloom_level:
        violations.append(Violation("bloom_level", constraint.bloom_level, observed_bloom or "unclassified"))
    elif _LEVEL_RANK[constraint.bloom_level] >= _LEVEL_RANK["apply"] and (asked := procedure_ask(stem)):
        # The verb is right and the task is not: from `apply` upwards the student has to produce
        # the result, and a description of the method is a lower level whatever opens the stem.
        violations.append(Violation(
            "bloom_level", f"{constraint.bloom_level}: ask for the result itself",
            f"opens at {constraint.bloom_level} but asks to \u201c{asked}\u201d, a description of a procedure (understand)",
        ))

    # question_type
    checks += 1
    has_digits_stem = bool(re.search(r"\d", stem))
    has_digits_answer = bool(re.search(r"\d", answer_key))
    if constraint.question_type == "mcq":
        if not options or len(options) != 4:
            violations.append(Violation("question_type", "mcq with 4 options", f"{len(options or [])} options"))
        elif answer_key.strip() not in [o.strip() for o in options] and answer_key.strip().upper() not in ("A", "B", "C", "D"):
            violations.append(Violation("question_type", "mcq answer_key is one of the options", "answer_key not among options"))
    else:
        if options:
            violations.append(Violation("question_type", constraint.question_type, f"mcq-style with {len(options)} options"))
        elif constraint.question_type == "numerical" and not (has_digits_stem and has_digits_answer):
            violations.append(Violation("question_type", "numerical (numbers in stem and answer)", "no numeric content"))

    # marks (skip for mcq: the answer key is a single option)
    if constraint.question_type != "mcq":
        checks += 1
        lo, hi = _mark_band(constraint.marks)
        n = len(answer_key.split())
        if not (lo <= n <= hi):
            violations.append(Violation("marks", f"{constraint.marks} marks -> answer of {lo}-{hi} words", f"{n} words"))

    # concept
    checks += 1
    if not _overlap(constraint.concept, body):
        violations.append(Violation("concept", constraint.concept, "concept terms absent from question and answer"))

    # course_outcome
    checks += 1
    if not _overlap(constraint.course_outcome, body):
        violations.append(Violation("course_outcome", constraint.course_outcome, "outcome terms absent from question and answer"))

    # grounding
    checks += 1
    if best_support is None:
        violations.append(Violation("grounding", f"verbatim quote from a cited passage with support >= {settings.grounding_min_similarity}", "no citation with a verbatim quote"))
    elif best_support < settings.grounding_min_similarity:
        violations.append(Violation("grounding", f"support >= {settings.grounding_min_similarity}", f"best support {best_support:.2f}"))

    passed = checks - len(violations)
    return Report(is_compliant=not violations, satisfaction_rate=passed / checks, violations=violations)
