"""Past-year question corpus: upload a paper, review what was extracted, list the corpus."""

import shutil
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..models import PYQ, DuplicatePair, NoveltyReport, PYQPaper, User
from ..schemas import PYQOut, PYQPaperOut, PYQPasteIn
from ..scoping import COORDINATOR, require_course, require_paper
from ..services import pyq_ingestion

router = APIRouter(tags=["pyqs"])

_SOURCE_TYPES = {".pdf": "pdf", ".docx": "docx"}


def _paper_out(db: Session, paper: PYQPaper) -> PYQPaperOut:
    count = db.scalar(select(func.count(PYQ.pyq_id)).where(PYQ.paper_id == paper.paper_id)) or 0
    return PYQPaperOut(
        paper_id=paper.paper_id, year=paper.year, label=paper.label, filename=paper.filename,
        source_type=paper.source_type, uploaded_at=paper.uploaded_at, question_count=count,
    )


@router.post("/courses/{course_id}/pyq-papers", response_model=PYQPaperOut, status_code=201)
def upload_paper(course_id: UUID, file: UploadFile, year: int = Form(...), label: str = Form(...),
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_course(db, user, course_id, COORDINATOR)
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in _SOURCE_TYPES:
        raise HTTPException(400, "only .pdf and .docx are accepted")
    dest_dir = settings.storage_dir / str(course_id) / "pyq"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4()}{ext}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    paper = PYQPaper(course_id=course_id, year=year, label=label, filename=file.filename,
                     source_type=_SOURCE_TYPES[ext], stored_path=str(dest))
    db.add(paper)
    db.commit()
    text = pyq_ingestion.read_text(dest, paper.source_type)
    paper.detected_year = pyq_ingestion.detect_year(text)
    if pyq_ingestion.ingest_paper(db, paper, text) == 0:
        db.delete(paper)
        db.commit()
        raise HTTPException(422, "no questions could be extracted; paste the questions as text instead")
    db.refresh(paper)
    return _paper_out(db, paper)


@router.post("/courses/{course_id}/pyq-papers/text", response_model=PYQPaperOut, status_code=201)
def paste_paper(course_id: UUID, body: PYQPasteIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """For papers whose layout defeats the splitter: one question per blank-line-separated block."""
    require_course(db, user, course_id, COORDINATOR)
    paper = PYQPaper(course_id=course_id, year=body.year, label=body.label, source_type="text")
    paper.detected_year = pyq_ingestion.detect_year(body.text)
    db.add(paper)
    db.commit()
    if pyq_ingestion.ingest_paper(db, paper, body.text) == 0:
        db.delete(paper)
        db.commit()
        raise HTTPException(
            422,
            "No questions were recognised in that text. Separate each question with a blank line "
            "and start it with its number, e.g. 'Q1. ...' or '1. ...'.",
        )
    db.refresh(paper)
    return _paper_out(db, paper)


@router.get("/courses/{course_id}/pyq-papers", response_model=list[PYQPaperOut])
def list_papers(course_id: UUID, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    require_course(db, user, course_id)
    papers = db.scalars(
        select(PYQPaper).where(PYQPaper.course_id == course_id).order_by(PYQPaper.year.desc(), PYQPaper.label)
    ).all()
    return [_paper_out(db, p) for p in papers]


@router.get("/pyq-papers/{paper_id}/questions", response_model=list[PYQOut])
def list_questions(paper_id: UUID, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    require_paper(db, user, paper_id)
    return db.scalars(select(PYQ).where(PYQ.paper_id == paper_id).order_by(PYQ.number)).all()


@router.delete("/pyq-papers/{paper_id}", status_code=204)
def delete_paper(paper_id: UUID, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    """Removing a mis-extracted paper. Labelled pairs already built from it keep their text, so a
    fold assignment is never invalidated by a corpus correction."""
    paper = require_paper(db, user, paper_id, COORDINATOR)
    ids = select(PYQ.pyq_id).where(PYQ.paper_id == paper_id)
    db.query(DuplicatePair).filter(DuplicatePair.left_pyq_id.in_(ids)).update(
        {"left_pyq_id": None}, synchronize_session=False
    )
    db.query(DuplicatePair).filter(DuplicatePair.right_pyq_id.in_(ids)).update(
        {"right_pyq_id": None}, synchronize_session=False
    )
    db.query(NoveltyReport).filter(NoveltyReport.nearest_pyq_id.in_(ids)).update(
        {"nearest_pyq_id": None}, synchronize_session=False
    )
    db.delete(paper)
    db.commit()
