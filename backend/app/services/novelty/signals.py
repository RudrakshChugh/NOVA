"""The four duplicate-detection signals of plan §3.1. Each returns a value in [0, 1];
the combiner (combiner.py) learns how to weigh them, so nothing here is tuned by hand.

  semantic    embedding cosine similarity
  lexical     Okapi BM25 term overlap against the past-year corpus
  structural  extracted metadata: type, marks, Bloom level, and clause set (order-insensitive,
              which is what catches a question whose conditions were reordered)
  numerical   similarity after masking every number, which is what catches a question
              that was reused with different numbers
"""

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

SIGNAL_NAMES = ("semantic", "lexical", "structural", "numerical")

_TOKEN = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CLAUSE_SPLIT = re.compile(r"[;,:]|\band\b|\bor\b|\n")
_STOPWORDS = frozenset(
    """a an the and or but if then than that this these those of in on at to for from by with
    without into over under between among is are was were be been being do does did have has had
    it its as not no any all each both such same so very can could may might must shall should
    will would you your we our they their he she his her i me my""".split()
)


def tokenize(text: str, drop_stopwords: bool = True) -> list[str]:
    tokens = _TOKEN.findall(text.lower())
    return [t for t in tokens if not drop_stopwords or t not in _STOPWORDS]


def mask_numbers(text: str) -> str:
    return _NUMBER.sub("#", text)


def numbers_in(text: str) -> list[str]:
    return _NUMBER.findall(text)


@dataclass(frozen=True)
class Metadata:
    """The structural attributes compared between two questions. Unknown fields are skipped."""

    question_type: str | None = None
    marks: int | None = None
    bloom_level: str | None = None


# --- semantic -----------------------------------------------------------------------------


def semantic(left_vec: list[float], right_vec: list[float]) -> float:
    """Cosine similarity of two L2-normalised embeddings, clipped to [0, 1]."""
    dot = sum(a * b for a, b in zip(left_vec, right_vec))
    return max(0.0, min(1.0, dot))


# --- lexical ------------------------------------------------------------------------------


class BM25Index:
    """Okapi BM25 over the past-year corpus.

    BM25 is a ranking score, not a bounded similarity, so a pair value is normalised by the score
    the query would earn against itself: `min(1, score(q, d) / score(q, q))`. Both use the same IDF
    weights, so the ratio reports how much of the query's own evidence the document supplies.
    Term saturation lets a short, repetitive document exceed the self-score, hence the clip.
    """

    def __init__(self, documents: dict[str, str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.doc_tokens = {key: tokenize(text) for key, text in documents.items()}
        self.doc_len = {key: len(toks) for key, toks in self.doc_tokens.items()}
        self.avgdl = (sum(self.doc_len.values()) / len(self.doc_len)) if self.doc_len else 0.0
        self.freqs = {key: _counts(toks) for key, toks in self.doc_tokens.items()}
        self.n_docs = len(self.doc_tokens)
        df: dict[str, int] = {}
        for counts in self.freqs.values():
            for term in counts:
                df[term] = df.get(term, 0) + 1
        self.df = df

    def _idf(self, term: str) -> float:
        # Robertson/Sparck-Jones IDF with the +0.5 smoothing, floored at 0 so a term present in
        # every document cannot contribute negative evidence.
        n = self.df.get(term, 0)
        return max(0.0, math.log((self.n_docs - n + 0.5) / (n + 0.5) + 1.0))

    def _score(self, query_terms: list[str], counts: dict[str, int], length: int) -> float:
        if not length or not self.avgdl:
            return 0.0
        norm = self.k1 * (1 - self.b + self.b * length / self.avgdl)
        total = 0.0
        for term in set(query_terms):
            f = counts.get(term, 0)
            if f:
                total += self._idf(term) * (f * (self.k1 + 1)) / (f + norm)
        return total

    def lexical(self, query_text: str, doc_text: str) -> float:
        """Normalised BM25 of `query_text` against `doc_text`. The document need not be a corpus
        member — the corpus only supplies the IDF weights and the average document length — so
        the same statistics score a candidate question and a constructed pair variant alike."""
        terms = tokenize(query_text)
        doc_tokens = tokenize(doc_text)
        if not terms or not doc_tokens:
            return 0.0
        self_score = self._score(terms, _counts(terms), len(terms))
        if self_score <= 0:
            return 0.0
        score = self._score(terms, _counts(doc_tokens), len(doc_tokens))
        return max(0.0, min(1.0, score / self_score))


def _counts(tokens: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tokens:
        out[t] = out.get(t, 0) + 1
    return out


# --- structural ---------------------------------------------------------------------------


def _trim_common(left: str, right: str) -> tuple[str, str]:
    """Drop the wording the two questions share at each end before splitting into clauses.

    A question's lead-in ("Design a schema that ...") binds to whichever condition happens to come
    first, so without this the same conditions in a different order yield different first clauses.
    Trimming isolates the part that actually differs. If either side is left empty the texts are
    returned untouched."""
    a, b = left.split(), right.split()
    start = 0
    while start < len(a) and start < len(b) and a[start].lower() == b[start].lower():
        start += 1
    end = 0
    while (
        end < len(a) - start and end < len(b) - start
        and a[len(a) - 1 - end].lower() == b[len(b) - 1 - end].lower()
    ):
        end += 1
    mid_a, mid_b = a[start : len(a) - end], b[start : len(b) - end]
    if not mid_a or not mid_b:
        return left, right
    return " ".join(mid_a), " ".join(mid_b)


def _clause_set(text: str) -> set[str]:
    """Clauses as order-insensitive keys: two questions listing the same conditions in a
    different order produce the same set."""
    out = set()
    for clause in _CLAUSE_SPLIT.split(text.lower()):
        tokens = tokenize(clause)
        if tokens:
            out.add(" ".join(tokens))
    return out


def structural(left_text: str, right_text: str, left: Metadata, right: Metadata) -> float:
    """Mean of the comparable metadata components. Components whose value is unknown on either
    side are skipped rather than scored as a mismatch."""
    parts: list[float] = []
    if left.question_type and right.question_type:
        parts.append(1.0 if left.question_type == right.question_type else 0.0)
    if left.marks and right.marks:
        parts.append(1.0 - abs(left.marks - right.marks) / max(left.marks, right.marks))
    if left.bloom_level and right.bloom_level:
        parts.append(1.0 if left.bloom_level == right.bloom_level else 0.0)
    trimmed_left, trimmed_right = _trim_common(left_text, right_text)
    a, b = _clause_set(trimmed_left), _clause_set(trimmed_right)
    if a or b:
        parts.append(len(a & b) / len(a | b))
    return sum(parts) / len(parts) if parts else 0.0


# --- numerical ----------------------------------------------------------------------------


def numerical(left_text: str, right_text: str) -> float:
    """Token-sequence similarity after masking every number. A question reused with new numbers
    keeps a near-identical masked form, so this stays high where the raw text diverges."""
    left = tokenize(mask_numbers(left_text), drop_stopwords=False)
    right = tokenize(mask_numbers(right_text), drop_stopwords=False)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


# --- vector -------------------------------------------------------------------------------


def signal_vector(
    left_text: str,
    right_text: str,
    left_vec: list[float],
    right_vec: list[float],
    left_meta: Metadata,
    right_meta: Metadata,
    index: BM25Index | None,
) -> dict[str, float]:
    """All four signals for one pair, keyed by SIGNAL_NAMES."""
    return {
        "semantic": semantic(left_vec, right_vec),
        "lexical": index.lexical(left_text, right_text) if index else 0.0,
        "structural": structural(left_text, right_text, left_meta, right_meta),
        "numerical": numerical(left_text, right_text),
    }
