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
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BLOOM_LEVEL = {
    "remember": "L1", "understand": "L2", "apply": "L3",
    "analyze": "L4", "evaluate": "L5", "create": "L6",
}

_base = getSampleStyleSheet()

# An Indian university question paper is set in Times and ruled in black. The layout below is
# the one the institute's own papers use — roll-number box, institution and department centred,
# a two-column block for the examination and the course, the note to candidates, then one ruled
# table of questions — so a paper printed here sits in a file next to hand-set ones without
# looking like it came from somewhere else.
SERIF, SERIF_BOLD, SERIF_ITALIC = "Times-Roman", "Times-Bold", "Times-Italic"
INK = colors.black
WIDTH = 174 * mm  # A4 less 18 mm margins

INSTITUTION = ParagraphStyle("paper-inst-name", parent=_base["Normal"], fontName=SERIF_BOLD, fontSize=14.5,
                             leading=18, alignment=TA_CENTER)
DEPARTMENT = ParagraphStyle("paper-dept", parent=_base["Normal"], fontName=SERIF_BOLD, fontSize=11.5,
                            leading=15, alignment=TA_CENTER)
META = ParagraphStyle("paper-meta", parent=_base["Normal"], fontName=SERIF, fontSize=10.5, leading=14)
NOTE = ParagraphStyle("paper-note", parent=_base["Normal"], fontName=SERIF, fontSize=10.5, leading=14)
HEAD = ParagraphStyle("paper-head", parent=_base["Normal"], fontName=SERIF_BOLD, fontSize=10.5, leading=13,
                      alignment=TA_CENTER)
BODY = ParagraphStyle("paper-body", parent=_base["Normal"], fontName=SERIF, fontSize=11, leading=15,
                      alignment=TA_JUSTIFY)
OPTION = ParagraphStyle("paper-option", parent=_base["Normal"], fontName=SERIF, fontSize=11, leading=15)
CELL = ParagraphStyle("paper-cell", parent=_base["Normal"], fontName=SERIF, fontSize=11, leading=15,
                      alignment=TA_CENTER)
QNO = ParagraphStyle("paper-qno", parent=CELL, fontName=SERIF_BOLD)
ANSWER = ParagraphStyle("paper-answer", parent=_base["Normal"], fontName=SERIF, fontSize=10, leading=13.5,
                        alignment=TA_JUSTIFY, textColor=colors.HexColor("#1a1a1a"))
BANNER = ParagraphStyle("paper-banner", parent=_base["Normal"], fontName=SERIF_BOLD, fontSize=10.5, leading=14,
                        alignment=TA_CENTER)

GRID = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.7, INK),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
    ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
])

# The standard Times face has no glyph for these, and prints a black box in their place. A
# model writes them freely ("data‑flow diagram"), so they are mapped before anything is drawn.
_UNPRINTABLE = {
    "\u2011": "-", "\u2010": "-", "\u2012": "-", "\u2212": "-", "\u00ad": "",
    "\u202f": " ", "\u2009": " ", "\u200a": " ", "\u2007": " ", "\u00a0": " ", "\u200b": "",
    "\u2192": "->", "\u2264": "<=", "\u2265": ">=", "\u2260": "!=", "\u00d7": "x",
}


def _escape(text: str) -> str:
    text = text or ""
    for bad, good in _UNPRINTABLE.items():
        text = text.replace(bad, good)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def _label(item: dict) -> str:
    return f"Q{item['number']}{item.get('part') or ''}"


def _duration(minutes: int | None) -> str:
    if not minutes:
        return ""
    hours, rest = divmod(int(minutes), 60)
    parts = []
    if hours:
        parts.append(f"{hours} Hour{'s' if hours != 1 else ''}")
    if rest:
        parts.append(f"{rest} Minutes")
    return " ".join(parts)


def _question_cell(item: dict, with_answer: bool) -> list:
    flow: list = [Paragraph(_escape(item["stem"]), BODY)]
    options = item.get("options") or []
    if options:
        # Two to a row, as a printed objective question sets them, unless one is too long to
        # share a line.
        labelled = [Paragraph(f"({chr(97 + i)}) {_escape(o)}", OPTION) for i, o in enumerate(options)]
        if max(len(o) for o in options) <= 38:
            rows = [labelled[i:i + 2] + [""] * (2 - len(labelled[i:i + 2])) for i in range(0, len(labelled), 2)]
            grid = Table(rows, colWidths=["50%", "50%"])
            grid.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            flow += [Spacer(1, 2), grid]
        else:
            flow += [Spacer(1, 2)] + labelled
    if with_answer:
        flow.append(Spacer(1, 5))
        flow.append(Paragraph(f"<b>Answer.</b> {_escape(item['answer_key'])}", ANSWER))
    return flow


def _header(snapshot: dict) -> list:
    template = snapshot.get("template") or {}
    course = snapshot.get("course") or {}
    out: list = []

    roll = Table([[Paragraph("Roll Number:", META), ""]], colWidths=[24 * mm, 46 * mm], hAlign="LEFT")
    roll.setStyle(TableStyle([
        ("BOX", (1, 0), (1, 0), 0.7, INK), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    out += [roll, Spacer(1, 6)]

    if template.get("institution"):
        out.append(Paragraph(_escape(template["institution"]), INSTITUTION))
    if template.get("department"):
        out.append(Paragraph(_escape(template["department"]), DEPARTMENT))
    out.append(Spacer(1, 5))

    # Examination on the left, course on the right; time and marks under them. Empty cells are
    # simply left out, so a subject with no template still prints a tidy block.
    exam = " \u2013 ".join(x for x in (template.get("exam_name"), snapshot.get("exam_date")) if x) \
        or (snapshot.get("title") or "")
    time = _duration(template.get("duration_minutes"))
    cells = [
        [f"<b>{_escape(exam)}</b>" if exam else "", f"Course Code: <b>{_escape(course.get('code') or '')}</b>" if course.get("code") else ""],
        [f"Time: {time}" if time else "", f"Course Name: <b>{_escape(course.get('title') or '')}</b>" if course.get("title") else ""],
        ["", f"Maximum Marks: <b>{snapshot.get('total_marks', 0)}</b>"],
    ]
    if not time:  # keep marks on the left when there is no duration to put there
        cells[1][0], cells = f"Maximum Marks: <b>{snapshot.get('total_marks', 0)}</b>", cells[:2]
    cells = [row for row in cells if any(row)]
    block = Table([[Paragraph(a, META), Paragraph(b, META)] for a, b in cells], colWidths=[WIDTH * 0.5, WIDTH * 0.5])
    block.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 1.2, INK), ("LINEBELOW", (0, -1), (-1, -1), 1.2, INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, 0), 5), ("BOTTOMPADDING", (0, -1), (-1, -1), 5),
    ]))
    out += [block, Spacer(1, 6)]
    if template.get("instructions"):
        out.append(Paragraph(f"<b>Note:</b> {_escape(template['instructions'])}", NOTE))
        out.append(Spacer(1, 7))
    return out


class _Numbered(pdf_canvas.Canvas):
    """"Page 2 of 3" needs the total, which is only known once every page has been laid out, so
    pages are held back and the footer drawn on each at the end."""

    def __init__(self, *args, footer: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._pages: list[dict] = []
        self._footer = footer

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self.setFont(SERIF, 9)
            self.setFillColor(colors.HexColor("#333333"))
            self.drawString(18 * mm, 10 * mm, self._footer)
            self.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def render(snapshot: dict, with_answers: bool = False) -> bytes:
    """The paper as PDF bytes. `with_answers` renders the marking scheme alongside each question."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm, bottomMargin=17 * mm,
        title=snapshot.get("title") or "Question paper",
    )
    flow = _header(snapshot)
    if with_answers:
        banner = Table([[Paragraph("MARKING SCHEME AND ANSWER KEY \u2013 not for distribution to candidates", BANNER)]], colWidths=[WIDTH])
        banner.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.7, INK), ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eeeeee")),
                                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        flow += [banner, Spacer(1, 7)]

    # The outcome and Bloom columns are the setter's working. By default they stay off the
    # candidate's paper and on the coordinator's copy, which is the one an accreditation file
    # wants. Some institutes print them on the paper itself — the past papers this subject was
    # tested with do — so the subject's template can switch them on.
    template = snapshot.get("template") or {}
    mapping = with_answers or bool(template.get("print_mapping"))
    head = ["Q. No.", "Questions", "Marks"] + (["CO", "BL"] if mapping else [])
    rows: list = [[Paragraph(h, HEAD) for h in head]]
    for item in snapshot.get("items", []):
        row = [
            Paragraph(_label(item), QNO),
            _question_cell(item, with_answers),
            Paragraph(str(item.get("marks", "")), CELL),
        ]
        if mapping:
            row += [
                Paragraph(_escape((item.get("course_outcome") or "").split(" ", 1)[0]), CELL),
                Paragraph(BLOOM_LEVEL.get(item.get("bloom_level"), ""), CELL),
            ]
        rows.append(row)
    widths = [15, 116, 15, 14, 14] if mapping else [15, 143, 16]
    table = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(GRID)
    flow.append(table)

    course = snapshot.get("course") or {}
    footer = " \u2013 ".join(x for x in (course.get("code"), template.get("exam_name") or snapshot.get("title")) if x)
    doc.build(flow, canvasmaker=lambda *a, **k: _Numbered(*a, footer=footer, **k))
    return buffer.getvalue()
