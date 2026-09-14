import re
import shutil
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_role
from ..config import settings
from ..db import get_db
from ..models import Chunk, Course, Material
from ..schemas import ChunkOut, CourseOut, MaterialOut
from ..services import ingestion

router = APIRouter(tags=["materials"])

_SOURCE_TYPES = {".pdf": "pdf", ".docx": "docx"}


@router.get("/courses", response_model=list[CourseOut])
def list_courses(db: Session = Depends(get_db)):
    return db.scalars(select(Course).order_by(Course.code)).all()


def _pages(refs: list[str]) -> int:
    """Distinct pages/paragraphs touched by chunk refs such as 'p3', 'p3-p5', 'para7-para9'."""
    covered: set[int] = set()
    for ref in refs:
        nums = [int(n) for n in re.findall(r"\d+", ref)]
        covered.update(range(nums[0], nums[-1] + 1))
    return len(covered)


def _material_out(db: Session, m: Material) -> MaterialOut:
    refs = db.scalars(select(Chunk.page_ref).where(Chunk.material_id == m.material_id)).all()
    chunk_count, pages = len(refs), _pages(refs)
    return MaterialOut(
        material_id=m.material_id, filename=m.filename, source_type=m.source_type,
        uploaded_at=m.uploaded_at, chunk_count=chunk_count, pages_covered=pages,
    )


@router.post("/courses/{course_id}/materials", response_model=MaterialOut, status_code=201, dependencies=[Depends(require_role("coordinator"))])
def upload_material(course_id: UUID, file: UploadFile, db: Session = Depends(get_db)):
    if db.get(Course, course_id) is None:
        raise HTTPException(404, "course not found")
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in _SOURCE_TYPES:
        raise HTTPException(400, "only .pdf and .docx are accepted")
    dest_dir = settings.storage_dir / str(course_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4()}{ext}"
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    material = Material(course_id=course_id, filename=file.filename, source_type=_SOURCE_TYPES[ext], stored_path=str(dest))
    db.add(material)
    db.commit()
    ingestion.ingest(db, material)
    db.refresh(material)
    return _material_out(db, material)


@router.get("/courses/{course_id}/materials", response_model=list[MaterialOut])
def list_materials(course_id: UUID, db: Session = Depends(get_db)):
    rows = db.scalars(select(Material).where(Material.course_id == course_id).order_by(Material.uploaded_at)).all()
    return [_material_out(db, m) for m in rows]


@router.get("/materials/{material_id}/chunks", response_model=list[ChunkOut])
def preview_chunks(material_id: UUID, limit: int = 5, db: Session = Depends(get_db)):
    """A few indexed passages, so ingestion is visibly real (plan §7.1 step 3)."""
    if db.get(Material, material_id) is None:
        raise HTTPException(404, "material not found")
    return db.scalars(select(Chunk).where(Chunk.material_id == material_id).order_by(Chunk.page_ref).limit(limit)).all()
