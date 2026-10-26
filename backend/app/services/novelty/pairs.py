"""Construction and splitting of the labelled duplicate-pair set (plan §5, §11 step 1).

Positives are built by transforming a real past-year question, so the label is known by
construction; the stratum records how hard the transform makes the pair:

  surface   formatting, numbering prefix, filler words — a duplicate any method should catch
  hard      renumbered, clauses reordered, synonyms substituted, or an LLM paraphrase — the
            cases plan §3.1 says a single embedding threshold sits below

Negatives are real pairs of different questions. Easy negatives are sampled across topics;
hard negatives are the most semantically similar non-duplicates, which is what stops the task
from being solvable by cosine similarity alone.

Split assignment is a pure function of (seed, pair_key), so a pair's fold never changes when
other pairs are added and the split can never be silently re-drawn. Stratification is therefore
approximate rather than exact; `composition()` reports what each fold actually contains.
"""

import hashlib
import random
import re
from dataclasses import dataclass

from .signals import mask_numbers, tokenize

SPLIT_SEED = 20260915
TRAIN_FRACTION, VALIDATION_FRACTION = 0.60, 0.20  # remainder is test
SURFACE, HARD = "surface", "hard"

SYNONYMS = {
    "explain": "describe", "describe": "explain", "define": "state the definition of",
    "list": "enumerate", "enumerate": "list", "calculate": "compute", "compute": "calculate",
    "show": "demonstrate", "demonstrate": "show", "discuss": "elaborate on",
    "briefly": "in short", "diagram": "figure", "figure": "diagram", "method": "technique",
    "technique": "method", "advantages": "benefits", "benefits": "advantages",
    "disadvantages": "drawbacks", "drawbacks": "disadvantages", "main": "primary",
    "primary": "main", "different": "various", "various": "different", "write": "give",
    "steps": "stages", "stages": "steps", "example": "instance", "instance": "example",
}

_CLAUSE = re.compile(r"\s*;\s*|\s*,\s+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class ConstructedPair:
    left_text: str
    right_text: str
    label: bool
    stratum: str
    provenance: str
    left_pyq_id: object | None = None
    right_pyq_id: object | None = None


# --- stable identity and split ---------------------------------------------------------------


def pair_key(left: str, right: str) -> str:
    """Order-independent identity of a pair, stable across machines and databases."""
    a, b = sorted(" ".join(tokenize(t, drop_stopwords=False)) for t in (left, right))
    return hashlib.sha256(f"{a}||{b}".encode()).hexdigest()[:32]


def assign_split(key: str, seed: int = SPLIT_SEED) -> str:
    """Deterministic fold for a pair key. Depends on nothing but the key and the seed."""
    digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    position = int.from_bytes(digest[:8], "big") / 2**64
    if position < TRAIN_FRACTION:
        return "train"
    if position < TRAIN_FRACTION + VALIDATION_FRACTION:
        return "val"
    return "test"


def composition(rows: list[tuple[str, str, bool]]) -> dict:
    """Per-fold counts by stratum and label, for the committed split manifest."""
    out: dict = {}
    for split, stratum, label in rows:
        bucket = out.setdefault(split, {"total": 0, "duplicate": 0, "non_duplicate": 0, SURFACE: 0, HARD: 0})
        bucket["total"] += 1
        bucket["duplicate" if label else "non_duplicate"] += 1
        bucket[stratum] += 1
    return out


# --- transforms that preserve "is a duplicate" ------------------------------------------------


def t_renumber(text: str, rng: random.Random) -> str | None:
    """Same question, different numbers — the case the numerical signal exists for."""
    numbers = _NUMBER.findall(text)
    if not numbers:
        return None
    out, last = [], 0
    for m in _NUMBER.finditer(text):
        value = m.group()
        if "." in value:
            new = f"{float(value) + rng.randint(1, 9):.1f}"
        else:
            new = str(max(1, int(value) + rng.randint(1, 20)))
        out.append(text[last : m.start()])
        out.append(new)
        last = m.end()
    out.append(text[last:])
    result = "".join(out)
    return result if result != text else None


def t_reorder_clauses(text: str, rng: random.Random) -> str | None:
    """Same conditions, listed in a different order."""
    clauses = [c for c in _CLAUSE.split(text) if c.strip()]
    if len(clauses) < 3:
        return None
    body, tail = clauses[:-1], clauses[-1]
    shuffled = body[:]
    for _ in range(8):
        rng.shuffle(shuffled)
        if shuffled != body:
            break
    else:
        return None
    return ", ".join(shuffled + [tail])


def t_synonyms(text: str, rng: random.Random) -> str | None:
    """Same question, different words."""
    replaced = False

    def swap(match: re.Match) -> str:
        nonlocal replaced
        word = match.group()
        alt = SYNONYMS.get(word.lower())
        if alt is None:
            return word
        replaced = True
        return alt.capitalize() if word[0].isupper() else alt

    result = re.sub(r"[A-Za-z]+", swap, text)
    return result if replaced and result != text else None


def t_question_form(text: str, rng: random.Random) -> str | None:
    """Imperative rephrased as an interrogative, or vice versa."""
    stripped = text.strip().rstrip(".?")
    lowered = stripped.lower()
    for verb, template in (
        ("explain how ", "How does {rest}? Explain."),
        ("explain why ", "Why {rest}? Explain."),
        ("describe how ", "How does {rest}? Describe it."),
        ("state the ", "What is the {rest}?"),
        ("define ", "What is meant by {rest}?"),
        ("list ", "What are {rest}?"),
    ):
        if lowered.startswith(verb):
            return template.format(rest=stripped[len(verb) :].strip())
    return None


def t_prefix(text: str, rng: random.Random) -> str | None:
    """Same question under a different number/mark annotation — a surface duplicate."""
    return f"Q{rng.randint(1, 9)}. {text.strip()} [{rng.choice((2, 5, 10))} marks]"


def t_whitespace(text: str, rng: random.Random) -> str | None:
    """Reformatting only."""
    result = re.sub(r"\s+", "  ", text.strip()) + " "
    return result if result.strip() != text.strip() or result != text else result


SURFACE_TRANSFORMS = {"prefix": t_prefix, "whitespace": t_whitespace}
HARD_TRANSFORMS = {
    "renumber": t_renumber,
    "reorder_clauses": t_reorder_clauses,
    "synonyms": t_synonyms,
    "question_form": t_question_form,
}


def build_positives(
    questions: list[tuple[object, str]], rng: random.Random, per_question: int = 2
) -> list[ConstructedPair]:
    """Duplicate pairs from each source question, preferring hard transforms."""
    out: list[ConstructedPair] = []
    for pyq_id, text in questions:
        made = 0
        for stratum, transforms in ((HARD, HARD_TRANSFORMS), (SURFACE, SURFACE_TRANSFORMS)):
            for name, fn in transforms.items():
                if made >= per_question:
                    break
                variant = fn(text, rng)
                if variant and variant.strip() != text.strip():
                    out.append(ConstructedPair(text, variant, True, stratum, name, pyq_id, None))
                    made += 1
    return out


def build_negatives(
    questions: list[tuple[object, str]],
    neighbours: dict[object, list[tuple[object, float]]],
    rng: random.Random,
    hard_per_question: int = 1,
    easy_per_question: int = 1,
) -> list[ConstructedPair]:
    """Non-duplicate pairs. `neighbours` maps a question to its most semantically similar other
    questions; those become the hard negatives."""
    by_id = dict(questions)
    out: list[ConstructedPair] = []
    for pyq_id, text in questions:
        for other_id, _score in neighbours.get(pyq_id, [])[:hard_per_question]:
            if other_id != pyq_id and other_id in by_id:
                out.append(ConstructedPair(text, by_id[other_id], False, HARD, "hard_negative", pyq_id, other_id))
        pool = [q for q in questions if q[0] != pyq_id]
        for other_id, other_text in rng.sample(pool, min(easy_per_question, len(pool))):
            out.append(ConstructedPair(text, other_text, False, SURFACE, "sampled", pyq_id, other_id))
    return out


def dedupe(pairs: list[ConstructedPair]) -> list[ConstructedPair]:
    """One row per pair identity; a pair whose two sides are identical after masking numbers and
    normalising whitespace carries no information and is dropped."""
    seen: set[str] = set()
    out = []
    for p in pairs:
        left = " ".join(tokenize(mask_numbers(p.left_text), drop_stopwords=False))
        right = " ".join(tokenize(mask_numbers(p.right_text), drop_stopwords=False))
        if left == right and p.provenance != "renumber":
            continue
        key = pair_key(p.left_text, p.right_text)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out
