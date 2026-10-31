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

# --- institutional grid layout -------------------------------------------------------------
# Many departments set papers as a table: Q No. | Questions | Marks | CO | BL. Extracting text
# from such a PDF flattens the table, so the marks, outcome and Bloom cells arrive as their own
# short lines, interleaved anywhere inside the question they belong to. Splitting on general
# numbering then mistakes those cells, the header row, and any numbered list inside a question
# body for question boundaries. Recognising the layout is what makes the difference between a
# corpus of questions and a corpus of fragments.

_GRID_HEADER = re.compile(r"\bQues(?:tions?)?\b.*\bMarks\b.*\bCO\b.*\bBL\b", re.IGNORECASE)
# "Q1 ", "Q5. " and "Q2a " all start a question. The sub-part letter can follow the number
# with no separator at all, and missing that merges the question into the one before it.
_GRID_QUESTION = re.compile(r"^Q\s*\.?\s*(\d{1,2})\s*[.)]?\s*([a-z])?[.)]?\s+")
# A line that is nothing but table cells. OCR of this column is unreliable: CO5 prints as C05,
# and L1 as "L I" or "Ll", so the Bloom cell tolerates the letters those become.
_GRID_CELLS = re.compile(r"^(?:\s*(?:\d{1,2}|C[O0]\s?\d|L\s?[1-6IiJlL]))+\s*$")
_GRID_MARKS = re.compile(r"(?<![A-Za-z0-9])(\d{1,2})(?![A-Za-z0-9])")
_GRID_BLOOM = re.compile(r"L\s?([1-6IiJlL])", re.IGNORECASE)

# The level the paper itself assigns, which is better evidence than a cue verb in the stem.
BLOOM_BY_LEVEL = {
    "1": "remember", "2": "understand", "3": "apply",
    "4": "analyze", "5": "evaluate", "6": "create",
}


def _cell_marks(cell: str) -> int:
    """Marks on one table row.

    Page numbers have already been removed against the page they appeared on, so a bare number
    reaching here is a mark. Sub-parts are marked separately and their rows are summed. An outcome
    cell such as "C05" contributes nothing: its digits belong to the code, not to a figure.
    """
    return sum(int(m.group(1)) for m in _GRID_MARKS.finditer(cell))


def _bloom_from_cell(cells: str) -> str | None:
    match = _GRID_BLOOM.search(cells)
    if not match:
        return None
    # I, i, J, l and L are all what OCR makes of the 1 in L1.
    level = match.group(1)
    return BLOOM_BY_LEVEL.get("1" if level.upper() in {"I", "J", "L"} else level)
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
        # A page number is dropped against the page it appears on. In a paper laid out as a
        # table it is otherwise indistinguishable from a marks cell — both are a bare number
        # alone on a line — and counting one inflates the total the paper actually carries.
        out: list[str] = []
        for index, page in enumerate(PdfReader(str(path)).pages, start=1):
            out.extend(ln for ln in _lines(page.extract_text() or "") if ln.strip() != str(index))
        return "\n".join(out)
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


def split_grid_questions(text: str) -> list[ExtractedQuestion] | None:
    """Split a paper laid out as a Q No. / Questions / Marks / CO / BL table.

    Returns None when the layout is not that table, so the caller falls back to general
    numbering. Sub-parts a) and b) stay attached to the question that introduces them: they share
    its case study and are marked as one question on the paper.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    header = next((i for i, ln in enumerate(lines) if _GRID_HEADER.search(ln)), None)
    if header is None:
        return None

    blocks: list[tuple[str, list[str]]] = []
    for line in lines[header + 1:]:  # everything above the header is front matter
        match = _GRID_QUESTION.match(line)
        if match:
            number = match.group(1)
            # "Q2a" and "Q2b" are two rows of one question: it carries a single case study and is
            # numbered once on the paper, so they are joined rather than counted twice.
            if blocks and blocks[-1][0] == number:
                blocks[-1][1].append(line[match.end():].strip())
            else:
                blocks.append((number, [line[match.end():].strip()]))
        elif blocks:
            blocks[-1][1].append(line)
    if len(blocks) < 2:
        return None  # a header-like line in a paper that is not actually a grid

    out: list[ExtractedQuestion] = []
    for number, body in blocks:
        cells = [ln for ln in body if _GRID_CELLS.match(ln)]
        stem = re.sub(r"\s+", " ", " ".join(ln for ln in body if not _GRID_CELLS.match(ln))).strip()
        if len(stem.split()) < 4:
            continue
        # Sub-parts are marked separately, so the question is worth their sum.
        marks = sum(_cell_marks(cell) for cell in cells) or None
        bloom = _bloom_from_cell(" ".join(cells)) or classify_bloom(stem)
        out.append(
            ExtractedQuestion(
                number=number, text=stem, marks=marks,
                question_type=infer_type(stem, marks), bloom_level=bloom,
            )
        )
    return out or None


def split_questions(text: str) -> list[ExtractedQuestion]:
    """Split a paper into questions on its numbering; fall back to blank-line blocks."""
    grid = split_grid_questions(text)
    if grid is not None:
        return grid

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
