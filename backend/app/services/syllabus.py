"""Reading course outcomes out of a syllabus (plan.md §14.6).

Course outcomes used to be free text typed into every blueprint row, which meant the same outcome
was spelled three ways across one blueprint and the coverage map returned by validation could not
be trusted. They are now rows that belong to the subject, and the coordinator gets them by
uploading the syllabus they already have.

The extractor is rule-based, like the past-paper splitter and for the same reason: what it produces
becomes the vocabulary every blueprint is written against, so it has to be reviewable. It is also
deliberately generous about what it will match and honest about how sure it is — a wrong row that
is flagged costs a coordinator two seconds, and a missed row costs them a retype.

Two layouts cover nearly every syllabus seen so far:

  labelled   "CO1: Explain ...", "CO 2 - ...", "CO3 Apply ..."  — the label itself is the evidence
  listed     a "Course Outcomes" heading followed by a numbered or bulleted list

A labelled outcome is reported at full confidence. A listed one is reported at half confidence,
because the heading is the only thing saying those lines are outcomes at all.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from .ingestion import _lines, parse_docx, parse_pdf

# "CO1", "CO 1:", "CO-2 -", "C01" (the zero is what OCR makes of the letter O).
#
# Matched against the whole document rather than line by line. Text pulled out of a PDF does not
# keep the line breaks the page showed: an outcome can arrive mid-line, and a label can end up at
# the tail of the line before it. Anchoring to the start of a line therefore drops real outcomes
# from exactly the documents this is for.
_LABELLED = re.compile(
    r"(?:(?<=^)|(?<=[\s;:.)\]]))C[O0]\s*[-.]?\s*(\d{1,2})\s*[-:.)]?\s+", re.IGNORECASE
)

# The heading that introduces an unlabelled list.
_HEADING = re.compile(r"\bcourse\s+outcomes?\b|\blearning\s+outcomes?\b", re.IGNORECASE)

# A numbered or bulleted item under that heading.
_LISTED = re.compile(r"^(?:\(?(\d{1,2})[.)]|[-*•])\s+(.+)$")

# A line that ends the outcome block: another section heading, or a table header.
_SECTION_END = re.compile(
    r"\b(syllabus|unit\s+[ivx\d]|text\s*books?|reference|evaluation|assessment|"
    r"prerequisite|credits?|teaching\s+hours?|course\s+content)\b",
    re.IGNORECASE,
)

MIN_WORDS = 3
MAX_OUTCOMES = 20
LABELLED_CONFIDENCE = 1.0
LISTED_CONFIDENCE = 0.5


@dataclass
class ExtractedOutcome:
    code: str
    text: str
    confidence: float


def read_text(path: Path, source_type: str) -> str:
    """Document text with the same line repair course material gets, so a wrapped outcome arrives
    as one line rather than as two half-sentences."""
    sentences = parse_pdf(path) if source_type == "pdf" else parse_docx(path)
    return "\n".join(s.text for s in sentences)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" .;:-–—")


def _labelled(text: str) -> list[ExtractedOutcome]:
    """Every labelled outcome, each running from its own label to the next one.

    Taking the text between labels, rather than to the end of the line, is what makes a wrapped
    outcome come back whole without a separate continuation rule."""
    marks = list(_LABELLED.finditer(text))
    if not marks:
        return []
    out: list[ExtractedOutcome] = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[mark.end():end]
        # The last outcome runs into whatever section follows it, so it stops at that heading.
        cut = _SECTION_END.search(body)
        if cut is not None:
            body = body[: cut.start()]
        body = _clean(body)
        if len(body.split()) < MIN_WORDS:
            continue
        out.append(ExtractedOutcome(f"CO{int(mark.group(1))}", body, LABELLED_CONFIDENCE))
    return out


def _listed(lines: list[str]) -> list[ExtractedOutcome]:
    start = next((i for i, line in enumerate(lines) if _HEADING.search(line)), None)
    if start is None:
        return []
    out: list[ExtractedOutcome] = []
    for line in lines[start + 1:]:
        if _SECTION_END.search(line) and not _LISTED.match(line):
            break
        match = _LISTED.match(line)
        if match:
            body = _clean(match.group(2))
            if len(body.split()) >= MIN_WORDS:
                out.append(ExtractedOutcome(f"CO{len(out) + 1}", body, LISTED_CONFIDENCE))
        elif out and line[:1].islower():
            out[-1] = ExtractedOutcome(out[-1].code, _clean(f"{out[-1].text} {line}"), out[-1].confidence)
        elif out:
            break  # the list has ended
    return out


def extract_outcomes(text: str) -> list[ExtractedOutcome]:
    """Course outcomes read off a syllabus, best-effort and never silently.

    Labelled outcomes win outright: the label is direct evidence. The listed form is only tried
    when no label was found anywhere, because a document that labels its outcomes has already said
    which lines they are, and a numbered list elsewhere in it is something else.
    """
    lines = [line for line in _lines(text) if line.strip()]
    found = _labelled(chr(10).join(lines))
    if not found:
        found = _listed(lines)

    # One row per code, keeping the first: a syllabus often repeats its outcomes in a mapping table
    # further down, usually abbreviated.
    seen: dict[str, ExtractedOutcome] = {}
    for outcome in found:
        seen.setdefault(outcome.code, outcome)
    return sorted(seen.values(), key=lambda o: int(o.code[2:]))[:MAX_OUTCOMES]
