"""Past-year paper -> individual questions with the metadata the structural signal compares.

Splitting is rule-based rather than model-driven: an exam paper's question numbering and mark
annotations are regular, and a deterministic splitter is reviewable when the corpus it produces
is the ground truth a reported duplicate-detection score rests on. Papers that defeat the
patterns can be pasted as text, one question per blank-line-separated block.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from pypdf import PdfReader
from sqlalchemy.orm import Session

from ..models import PYQ, PYQPaper
from .compliance import classify_bloom
from .embeddings import embed
from .ingestion import _lines

# "1.", "1)", "Q1", "Q.1", "1 a)", "(a)", "iii)" at the start of a line. The sub-part letter must
# carry its own bracket or dot, or the first letter of the question itself is read as one.
_NUMBERING = re.compile(
    r"^(?:[Qq]\s*\.?\s*)?"
    r"(?:(\d{1,2})\s*[.)]\s*|(\d{1,2})\s+(?=\(?[a-z][.)]))"
    r"(?:\(?([a-z]|[ivx]{1,4})[.)]\s*)?"
    r"(?=[A-Z(\"'])"
    r"|^\(?([a-z]|[ivx]{1,4})[.)]\s*(?=[A-Z(\"'])"
)
_MARKS = re.compile(
    r"[\[(]\s*(\d{1,2})\s*(?:marks?|m)?\s*[\])]|[-–—]\s*(\d{1,2})\s*marks?\b|\b(\d{1,2})\s*marks?\b",
    re.IGNORECASE,
)
_MCQ_OPTIONS = re.compile(r"(?:^|\s)\(?[a-d]\)\s+\S", re.IGNORECASE)
_NUMERIC_CUE = re.compile(r"\b(calculate|compute|determine|find|evaluate the value|how many|what is the value)\b", re.IGNORECASE)


@dataclass
class ExtractedQuestion:
    number: str | None
    text: str
    marks: int | None
    question_type: str | None
    bloom_level: str | None


def read_text(path: Path, source_type: str) -> str:
    """Document text with slide/PDF line wrapping repaired, as in course-material ingestion."""
    if source_type == "pdf":
        return "\n".join(
            line for page in PdfReader(str(path)).pages for line in _lines(page.extract_text() or "")
        )
    if source_type == "docx":
        return "\n".join(line for p in Document(path).paragraphs for line in _lines(p.text))
    return path.read_text(encoding="utf-8", errors="replace")


def _strip_marks(text: str) -> tuple[str, int | None]:
    marks: int | None = None
    for m in _MARKS.finditer(text):
        value = next((g for g in m.groups() if g), None)
        if value:
            marks = int(value)
    cleaned = _MARKS.sub(" ", text) if marks is not None else text
    return re.sub(r"\s{2,}", " ", cleaned).strip(" .;:-–—"), marks


def infer_type(text: str, marks: int | None) -> str:
    if len(_MCQ_OPTIONS.findall(text)) >= 3:
        return "mcq"
    if _NUMERIC_CUE.search(text) and re.search(r"\d", text):
        return "numerical"
    if marks is not None and marks >= 6:
        return "long_answer"
    return "short_answer"


def split_questions(text: str) -> list[ExtractedQuestion]:
    """Split a paper into questions on its numbering; fall back to blank-line blocks."""
    lines = [ln.strip() for ln in text.splitlines()]
    blocks: list[tuple[str | None, list[str]]] = []
    for line in lines:
        if not line:
            if blocks:
                blocks[-1][1].append("")
            continue
        match = _NUMBERING.match(line)
        if match:
            number = "".join(g for g in match.groups() if g)
            blocks.append((number, [line[match.end() :].strip()]))
        elif blocks:
            blocks[-1][1].append(line)

    if not blocks:  # no numbering recognised: treat blank-line-separated blocks as questions
        blocks = [(None, block.splitlines()) for block in re.split(r"\n\s*\n", text) if block.strip()]

    out: list[ExtractedQuestion] = []
    for number, body in blocks:
        joined = re.sub(r"\s+", " ", " ".join(body)).strip()
        if len(joined.split()) < 4:  # headers, instructions, page furniture
            continue
        cleaned, marks = _strip_marks(joined)
        if len(cleaned.split()) < 4:
            continue
        out.append(
            ExtractedQuestion(
                number=number,
                text=cleaned,
                marks=marks,
                question_type=infer_type(cleaned, marks),
                bloom_level=classify_bloom(cleaned),
            )
        )
    return out


def ingest_paper(db: Session, paper: PYQPaper, text: str) -> int:
    """Store every extracted question with its embedding. Returns the count."""
    questions = split_questions(text)
    if not questions:
        return 0
    vectors = embed([q.text for q in questions])
    for q, vec in zip(questions, vectors):
        db.add(
            PYQ(
                paper_id=paper.paper_id, number=q.number, text=q.text, marks=q.marks,
                question_type=q.question_type, bloom_level=q.bloom_level, embedding=vec,
            )
        )
    db.commit()
    return len(questions)
