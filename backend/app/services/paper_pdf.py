"""Rendering an assembled paper as a PDF (plan.md §14.3).

Rendered on the server with ReportLab rather than by driving a headless browser. Chromium does not
fit beside the embedding model in the 512 MB the free tier gives, and asking each coordinator to
use their own browser's print dialog makes the output depend on which browser they happen to have.
ReportLab is already a dependency, draws the same bytes on every machine, and produces a file that
can be attached to an email or handed to a print room.

The layout follows the institutional grid the past-paper parser reads back: a numbered question,
its sub-parts, and a marks / CO / BL column on the right. A paper this system prints should be a
paper this system can read.
"""

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BLOOM_LEVEL = {
    "remember": "L1", "understand": "L2", "apply": "L3",
    "analyze": "L4", "evaluate": "L5", "create": "L6",
}

_base = getSampleStyleSheet()

# An Indian university question paper is set in Times, not in the Helvetica ReportLab
# defaults to, and the screen that assembles it is set in Times for the same reason.
SERIF, SERIF_BOLD = "Times-Roman", "Times-Bold"
TITLE = ParagraphStyle("paper-title", parent=_base["Title"], fontName=SERIF_BOLD, fontSize=14, leading=18, spaceAfter=2)
SUBTITLE = ParagraphStyle("paper-sub", parent=_base["Normal"], fontName=SERIF, fontSize=10.5, leading=14,
                          alignment=TA_CENTER, spaceAfter=1)
META = ParagraphStyle("paper-meta", parent=_base["Normal"], fontName=SERIF, fontSize=9.5, leading=13)
INSTRUCTION = ParagraphStyle("paper-inst", parent=_base["Normal"], fontName=SERIF, fontSize=9, leading=12.5,
                             textColor=colors.HexColor("#333333"))
BODY = ParagraphStyle("paper-body", parent=_base["Normal"], fontName=SERIF, fontSize=10, leading=14)
CELL = ParagraphStyle("paper-cell", parent=_base["Normal"], fontName=SERIF, fontSize=9, leading=12)
ANSWER = ParagraphStyle("paper-answer", parent=_base["Normal"], fontName=SERIF, fontSize=9.5, leading=13,
                        textColor=colors.HexColor("#1f4e79"))

HEADER_RULE = TableStyle([
    ("LINEBELOW", (0, 0), (-1, -1), 0.75, colors.HexColor("#444444")),
])

GRID = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ededed")),
    ("FONTNAME", (0, 0), (-1, 0), SERIF_BOLD),
    ("FONTSIZE", (0, 0), (-1, 0), 9),
    ("ALIGN", (2, 0), (-1, -1), "CENTER"),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
])


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _label(item: dict) -> str:
    return f"Q{item['number']}{item.get('part') or ''}"


def _question_cell(item: dict, with_answer: bool) -> list:
    flow: list = [Paragraph(_escape(item["stem"]), BODY)]
    for index, option in enumerate(item.get("options") or []):
        flow.append(Paragraph(f"({chr(97 + index)}) {_escape(option)}", CELL))
    if with_answer:
        flow.append(Spacer(1, 3))
        flow.append(Paragraph(f"<b>Answer.</b> {_escape(item['answer_key'])}", ANSWER))
    return flow


def _header(snapshot: dict) -> list:
    template = snapshot.get("template") or {}
    out: list = []
    if template.get("institution"):
        out.append(Paragraph(_escape(template["institution"]), TITLE))
    if template.get("department"):
        out.append(Paragraph(_escape(template["department"]), SUBTITLE))
    course = snapshot.get("course") or {}
    title_line = " — ".join(x for x in (course.get("code"), course.get("title")) if x)
    if title_line:
        out.append(Paragraph(_escape(title_line), SUBTITLE))
    if template.get("exam_name"):
        out.append(Paragraph(f"<b>{_escape(template['exam_name'])}</b>", SUBTITLE))
    out.append(Spacer(1, 6))

    left = []
    if snapshot.get("exam_date"):
        left.append(f"Date: {_escape(snapshot['exam_date'])}")
    if template.get("duration_minutes"):
        left.append(f"Duration: {template['duration_minutes']} minutes")
    right = [f"Maximum marks: {snapshot.get('total_marks', 0)}"]
    meta = Table(
        [[Paragraph("<br/>".join(left), META), Paragraph("<br/>".join(right), META)]],
        colWidths=[105 * mm, 65 * mm],
    )
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, -1), 0.75, colors.HexColor("#444444")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    out.append(meta)
    out.append(Spacer(1, 8))
    if template.get("instructions"):
        out.append(Paragraph(f"<b>Instructions.</b> {_escape(template['instructions'])}", INSTRUCTION))
        out.append(Spacer(1, 8))
    return out


def render(snapshot: dict, with_answers: bool = False) -> bytes:
    """The paper as PDF bytes. `with_answers` renders the answer key alongside each question."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=snapshot.get("title") or "Question paper",
    )
    flow = _header(snapshot)
    if with_answers:
        flow.append(Paragraph("<b>Answer key</b> — not for distribution to candidates.", INSTRUCTION))
        flow.append(Spacer(1, 6))

    rows = [["Q No.", "Questions", "Marks", "CO", "BL"]]
    for item in snapshot.get("items", []):
        rows.append([
            Paragraph(f"<b>{_label(item)}</b>", CELL),
            _question_cell(item, with_answers),
            str(item.get("marks", "")),
            _escape((item.get("course_outcome") or "").split(" ", 1)[0]),
            BLOOM_LEVEL.get(item.get("bloom_level"), ""),
        ])
    table = Table(rows, colWidths=[16 * mm, 113 * mm, 14 * mm, 14 * mm, 13 * mm], repeatRows=1)
    table.setStyle(GRID)
    flow.append(table)

    flow.append(Spacer(1, 10))
    flow.append(KeepTogether(Paragraph(
        f"Total: {snapshot.get('total_marks', 0)} marks over "
        f"{len(snapshot.get('items', []))} question(s).", META,
    )))
    doc.build(flow)
    return buffer.getvalue()
