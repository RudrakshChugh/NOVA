"""Parse PDF/DOCX -> page-tagged sentences -> sentence-window chunks -> embeddings -> chunks table.

Slide decks are common course material: bullets rarely end in a full stop, so bullet glyphs and
line breaks also delimit sentences, wrapped lines are re-joined, and a line repeated on most pages
(header/footer) is dropped."""

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from pypdf import PdfReader
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Chunk, Material
from .embeddings import embed

_BULLETS = re.compile("[•▪●■�]")  # incl. Wingdings/replacement glyphs from slide exports
_LEADING_DASH = re.compile(r"^[-–—]\s*")  # dash used as a bullet marker
_TERMINAL_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")
_BOILERPLATE_PAGE_FRACTION = 0.5


@dataclass
class Sentence:
    text: str
    page_ref: str


def _lines(text: str) -> list[str]:
    """Visual lines with bullets stripped; a line starting lowercase continues the previous one."""
    merged: list[str] = []
    for raw in _BULLETS.sub("\n", text).split("\n"):
        line = _LEADING_DASH.sub("", re.sub(r"\s+", " ", raw).strip())
        if not line:
            continue
        if merged and line[0].islower() and not re.search(r"[.!?:]$", merged[-1]):
            merged[-1] = f"{merged[-1]} {line}"
        else:
            merged.append(line)
    return merged


def _sentences_from_lines(lines: list[str], page_ref: str) -> list[Sentence]:
    out: list[Sentence] = []
    for line in lines:
        out.extend(Sentence(s.strip(), page_ref) for s in _TERMINAL_SPLIT.split(line) if len(s.strip()) > 3)
    return out


def parse_pdf(path: Path) -> list[Sentence]:
    pages = [_lines(page.extract_text() or "") for page in PdfReader(str(path)).pages]
    if len(pages) >= 4:
        freq = Counter(line for lines in pages for line in set(lines))
        boilerplate = {line for line, n in freq.items() if n >= _BOILERPLATE_PAGE_FRACTION * len(pages)}
        pages = [[line for line in lines if line not in boilerplate] for lines in pages]
    out: list[Sentence] = []
    for i, lines in enumerate(pages, start=1):
        out.extend(_sentences_from_lines(lines, f"p{i}"))
    return out


def parse_docx(path: Path) -> list[Sentence]:
    # DOCX has no fixed pagination; paragraph index is the stable locator.
    out: list[Sentence] = []
    for i, para in enumerate(Document(path).paragraphs, start=1):
        if para.text.strip():
            out.extend(_sentences_from_lines(_lines(para.text), f"para{i}"))
    return out


def sentence_windows(sentences: list[Sentence], window: int, stride: int) -> list[tuple[str, str]]:
    """Overlapping windows of `window` sentences advanced by `stride`.
    Returns (text, page_ref) where page_ref spans first..last page of the window."""
    chunks = []
    for start in range(0, max(len(sentences), 1), stride):
        group = sentences[start : start + window]
        if not group:
            break
        first, last = group[0].page_ref, group[-1].page_ref
        ref = first if first == last else f"{first}-{last}"
        chunks.append((" ".join(s.text for s in group), ref))
        if start + window >= len(sentences):
            break
    return chunks


def ingest(db: Session, material: Material) -> int:
    path = Path(material.stored_path)
    sentences = parse_pdf(path) if material.source_type == "pdf" else parse_docx(path)
    windows = sentence_windows(sentences, settings.chunk_window_sentences, settings.chunk_stride_sentences)
    vectors = embed([t for t, _ in windows])
    for (text, page_ref), vec in zip(windows, vectors):
        db.add(Chunk(material_id=material.material_id, text=text, page_ref=page_ref, embedding=vec))
    db.commit()
    return len(windows)
