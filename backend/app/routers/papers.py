"""Binding published questions into a paper, and printing it (plan.md §14.3).

The pipeline used to end at a bank entry, which is a row rather than anything a student could be
handed. This is the last step: choose published questions, number them, check that they add up to
the blueprint they were written for, and print.

Two rules carried over from the rest of the system. Only published questions may appear — a
question that no reviewer accepted has no business on a paper — and finalising freezes a snapshot,
so a paper that was printed stays reproducible after its questions are revised.
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import (
    Blueprint,
    CandidateQuestion,
    Course,
    GenerationRun,
    Paper,
    PaperItem,
    PaperTemplate,
    QuestionBankEntry,
    User,
)
from ..schemas import (
    PaperCheckOut,
    PaperCreateIn,
    PaperItemIn,
    PaperItemOut,
    PaperOut,
    PaperTemplateIn,
    PaperTemplateOut,
)
from ..scoping import COORDINATOR, REVIEWER, require_course
from ..services import paper_pdf

router = APIRouter(tags=["papers"])


# --- template ----------------------------------------------------------------


@router.get("/courses/{course_id}/paper-template", response_model=PaperTemplateOut)
def get_template(course_id: UUID, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    require_course(db, user, course_id)
    row = db.scalar(select(PaperTemplate).where(PaperTemplate.course_id == course_id))
    return row or PaperTemplateOut()


@router.put("/courses/{course_id}/paper-template", response_model=PaperTemplateOut)
def put_template(course_id: UUID, body: PaperTemplateIn, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    require_course(db, user, course_id, COORDINATOR)
    row = db.scalar(select(PaperTemplate).where(PaperTemplate.course_id == course_id))
    if row is None:
        row = PaperTemplate(course_id=course_id)
        db.add(row)
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


# --- assembly ----------------------------------------------------------------


def _require_paper(db: Session, user: User, paper_id: UUID, *roles: str) -> Paper:
    paper = db.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(404, "paper not found")
    # Scope is resolved from the paper's own subject, never from the request.
    from ..scoping import _guard

    _guard(db, user, paper.course_id, roles, "paper")
    return paper


def _item_out(item: PaperItem) -> PaperItemOut:
    snapshot = item.entry.snapshot
    constraint = snapshot.get("constraint", {})
    return PaperItemOut(
        item_id=item.item_id, entry_id=item.entry_id, position=item.position,
        number=item.number, part=item.part, marks=item.marks,
        stem=snapshot.get("stem", ""), options=snapshot.get("options"),
        answer_key=snapshot.get("answer_key", ""),
        course_outcome=constraint.get("course_outcome", ""),
        bloom_level=constraint.get("bloom_level", ""),
    )


def _paper_out(paper: Paper) -> PaperOut:
    items = [_item_out(i) for i in paper.items]
    return PaperOut(
        paper_id=paper.paper_id, course_id=paper.course_id, blueprint_id=paper.blueprint_id,
        title=paper.title, exam_date=paper.exam_date, state=paper.state,
        created_at=paper.created_at, finalised_at=paper.finalised_at,
        total_marks=sum(i.marks for i in items), items=items,
    )


def _published_for_blueprint(db: Session, blueprint_id: UUID) -> list[QuestionBankEntry]:
    """Every published question of one blueprint, in the order its rows were written.

    The latest entry per question only: a question republished after an edit appears once, as it
    now stands.
    """
    rows = db.scalars(
        select(QuestionBankEntry)
        .join(CandidateQuestion, CandidateQuestion.question_id == QuestionBankEntry.question_id)
        .join(GenerationRun, GenerationRun.run_id == CandidateQuestion.run_id)
        .where(GenerationRun.blueprint_id == blueprint_id)
        .order_by(QuestionBankEntry.published_at)
    ).all()
    latest: dict[UUID, QuestionBankEntry] = {}
    for row in rows:
        current = latest.get(row.question_id)
        if current is None or row.version > current.version:
            latest[row.question_id] = row
    return sorted(
        latest.values(),
        key=lambda e: (e.snapshot.get("constraint", {}).get("position") or 0, str(e.entry_id)),
    )


@router.post("/courses/{course_id}/papers", response_model=PaperOut, status_code=201)
def create_paper(course_id: UUID, body: PaperCreateIn, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """Start a paper. Naming a blueprint fills it with that blueprint's published questions, in
    blueprint order, which is the common case and saves choosing ten questions by hand."""
    require_course(db, user, course_id, COORDINATOR)
    paper = Paper(course_id=course_id, title=body.title.strip(), exam_date=body.exam_date,
                  blueprint_id=body.blueprint_id)
    db.add(paper)
    db.flush()

    if body.blueprint_id is not None:
        bp = db.get(Blueprint, body.blueprint_id)
        if bp is None or bp.course_id != course_id:
            raise HTTPException(404, "blueprint not found")
        for position, entry in enumerate(_published_for_blueprint(db, body.blueprint_id)):
            marks = entry.snapshot.get("constraint", {}).get("marks") or 1
            db.add(PaperItem(paper_id=paper.paper_id, entry_id=entry.entry_id, position=position,
                             number=position + 1, part=None, marks=marks))
    db.commit()
    db.refresh(paper)
    return _paper_out(paper)


@router.get("/courses/{course_id}/papers", response_model=list[PaperOut])
def list_papers(course_id: UUID, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    require_course(db, user, course_id)
    rows = db.scalars(
        select(Paper).where(Paper.course_id == course_id).order_by(Paper.created_at.desc())
    ).all()
    return [_paper_out(p) for p in rows]


@router.get("/papers/{paper_id}", response_model=PaperOut)
def get_paper(paper_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _paper_out(_require_paper(db, user, paper_id, REVIEWER, COORDINATOR))


@router.put("/papers/{paper_id}/items", response_model=PaperOut)
def put_items(paper_id: UUID, rows: list[PaperItemIn], db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    """Replace the contents, in the order given. Refused once the paper is final."""
    paper = _require_paper(db, user, paper_id, COORDINATOR)
    if paper.state == "final":
        raise HTTPException(409, "this paper is final; create another to change it")

    allowed = {
        e.entry_id
        for e in db.scalars(
            select(QuestionBankEntry)
            .join(CandidateQuestion, CandidateQuestion.question_id == QuestionBankEntry.question_id)
            .join(GenerationRun, GenerationRun.run_id == CandidateQuestion.run_id)
            .join(Blueprint, Blueprint.blueprint_id == GenerationRun.blueprint_id)
            .where(Blueprint.course_id == paper.course_id)
        ).all()
    }
    for i, row in enumerate(rows):
        if row.entry_id not in allowed:
            raise HTTPException(422, f"row {i + 1}: that question is not published on this subject")

    paper.items = [
        PaperItem(entry_id=r.entry_id, position=i, number=r.number,
                  part=(r.part or None), marks=r.marks)
        for i, r in enumerate(rows)
    ]
    db.commit()
    db.refresh(paper)
    return _paper_out(paper)


def _check(db: Session, paper: Paper) -> PaperCheckOut:
    out = _paper_out(paper)
    issues: list[str] = []
    if not paper.items:
        issues.append("the paper has no questions on it")

    seen: set[tuple[int, str | None]] = set()
    for item in out.items:
        key = (item.number, item.part)
        if key in seen:
            issues.append(f"Q{item.number}{item.part or ''} appears twice")
        seen.add(key)

    blueprint_marks = None
    if paper.blueprint_id is not None:
        bp = db.get(Blueprint, paper.blueprint_id)
        if bp is not None:
            blueprint_marks = sum(c.marks for c in bp.constraints)
            if blueprint_marks != out.total_marks:
                # The error this project exists to prevent, caught per question and then lost at
                # the last step, would be absurd.
                issues.append(
                    f"the paper carries {out.total_marks} marks but blueprint version "
                    f"{bp.version} specifies {blueprint_marks}"
                )
    return PaperCheckOut(ok=not issues, issues=issues, total_marks=out.total_marks,
                         blueprint_marks=blueprint_marks)


@router.post("/papers/{paper_id}/check", response_model=PaperCheckOut)
def check_paper(paper_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _check(db, _require_paper(db, user, paper_id, REVIEWER, COORDINATOR))


def _snapshot(db: Session, paper: Paper) -> dict:
    course = db.get(Course, paper.course_id)
    template = db.scalar(select(PaperTemplate).where(PaperTemplate.course_id == paper.course_id))
    out = _paper_out(paper)
    return {
        "title": paper.title,
        "exam_date": paper.exam_date,
        "total_marks": out.total_marks,
        "course": {"code": course.code, "title": course.title} if course else {},
        "blueprint_id": str(paper.blueprint_id) if paper.blueprint_id else None,
        "template": {
            "institution": template.institution if template else "",
            "department": template.department if template else "",
            "exam_name": template.exam_name if template else "",
            "duration_minutes": template.duration_minutes if template else None,
            "instructions": template.instructions if template else "",
        },
        "items": [
            {
                "entry_id": str(i.entry_id), "number": i.number, "part": i.part, "marks": i.marks,
                "stem": i.stem, "options": i.options, "answer_key": i.answer_key,
                "course_outcome": i.course_outcome, "bloom_level": i.bloom_level,
            }
            for i in out.items
        ],
    }


@router.post("/papers/{paper_id}/finalise", response_model=PaperOut)
def finalise(paper_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Freeze the paper. Refused while it does not add up."""
    paper = _require_paper(db, user, paper_id, COORDINATOR)
    if paper.state == "final":
        raise HTTPException(409, "already final")
    result = _check(db, paper)
    if not result.ok:
        raise HTTPException(422, {"issues": result.issues})
    paper.snapshot = _snapshot(db, paper)
    paper.state = "final"
    paper.finalised_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(paper)
    return _paper_out(paper)


# --- printing ----------------------------------------------------------------


def _pdf(db: Session, paper: Paper, with_answers: bool) -> Response:
    # A final paper prints from its snapshot, so it does not change under anyone who reprints it.
    snapshot = paper.snapshot or _snapshot(db, paper)
    pdf = paper_pdf.render(snapshot, with_answers=with_answers)
    suffix = "-answer-key" if with_answers else ""
    name = f"{(paper.title or 'paper').replace(' ', '-').lower()}{suffix}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/papers/{paper_id}/pdf")
def paper_pdf_download(paper_id: UUID, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    return _pdf(db, _require_paper(db, user, paper_id, REVIEWER, COORDINATOR), with_answers=False)


@router.get("/papers/{paper_id}/answer-key")
def answer_key_download(paper_id: UUID, db: Session = Depends(get_db),
                        user: User = Depends(current_user)):
    """The same paper with its answers. Coordinator-only: it is the one artefact here that must
    not be handed to a candidate."""
    return _pdf(db, _require_paper(db, user, paper_id, COORDINATOR), with_answers=True)


@router.delete("/papers/{paper_id}", status_code=204)
def delete_paper(paper_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    paper = _require_paper(db, user, paper_id, COORDINATOR)
    if paper.state == "final":
        raise HTTPException(409, "a final paper cannot be deleted")
    db.delete(paper)
    db.commit()
