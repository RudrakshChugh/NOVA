"""Course material: upload several files at once, and watch them being indexed (plan.md §14.2).

Two things changed when upload became plural. A corrupt file in position four must not discard the
other eleven, so every file carries its own outcome; and parse-chunk-embed moved out of the request
into a job row, because embedding is the slow step and a long synchronous upload is the thing a
free platform is most likely to cut off.
"""

import hashlib
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import settings
from ..db import SessionLocal, get_db
from ..models import Chunk, IngestionJob, Material, User
from ..schemas import ChunkOut, IngestionJobOut, MaterialOut
from ..scoping import COORDINATOR, require_course, require_material
from ..services import ingestion

router = APIRouter(tags=["materials"])

_SOURCE_TYPES = {".pdf": "pdf", ".docx": "docx"}
MAX_FILES = 25


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


def _store(course_id: UUID, file: UploadFile, ext: str) -> tuple[Path, str]:
    """Write the upload to disk and hash it on the way past, so the bytes are read once."""
    dest_dir = settings.storage_dir / str(course_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4()}{ext}"
    digest = hashlib.sha256()
    with dest.open("wb") as out:
        while chunk := file.file.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    return dest, digest.hexdigest()


def recover_interrupted_jobs() -> int:
    """Fail the ingestion jobs a restart left behind, for the same reason as generation runs:
    the task that would have finished them died with the process, and a row that says `queued`
    forever reads as "still working" to whoever uploaded the file."""
    with SessionLocal() as db:
        stuck = list(db.scalars(
            select(IngestionJob).where(IngestionJob.state.in_(("queued", "running")))
        ).all())
        for job in stuck:
            job.state = "failed"
            job.detail = "Interrupted: the server restarted before this file was indexed. Upload it again."
            job.finished_at = datetime.now(timezone.utc)
            material = db.get(Material, job.material_id) if job.material_id else None
            # Only a material with no passages is removed; one that finished indexing just
            # before the restart is real and stays.
            if material is not None and not db.scalar(
                select(Chunk.chunk_id).where(Chunk.material_id == material.material_id).limit(1)
            ):
                job.material_id = None
                db.flush()
                db.delete(material)
        db.commit()
        return len(stuck)


def run_jobs(job_ids: list[UUID]) -> None:
    """Index each queued file in turn, in a background task with its own session.

    Sequential rather than parallel on purpose: the embedder is the memory high-water mark of this
    process, and running several at once on a 512 MB instance is how the whole service dies rather
    than one upload failing.
    """
    db = SessionLocal()
    try:
        for job_id in job_ids:
            job = db.get(IngestionJob, job_id)
            if job is None or job.state != "queued":
                continue
            job.state = "running"
            db.commit()
            try:
                material = db.get(Material, job.material_id)
                job.chunk_count = ingestion.ingest(db, material)
                job.state = "done"
                job.detail = None
            except Exception as e:  # one bad file must not take the rest of the batch with it
                db.rollback()
                job = db.get(IngestionJob, job_id)
                job.state = "failed"
                job.detail = f"{type(e).__name__}: {e}"
                material = db.get(Material, job.material_id)
                if material is not None:
                    db.delete(material)  # a material with no passages is worse than none at all
                    job.material_id = None
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


@router.post("/courses/{course_id}/materials", response_model=list[IngestionJobOut], status_code=202)
def upload_materials(course_id: UUID, files: list[UploadFile], background: BackgroundTasks,
                     db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Accept a batch, store it, and queue each file for indexing.

    Returns one row per file, including the ones that were refused: a result the uploader can read
    file by file is the whole point, and an aggregate error on a batch of twelve says nothing about
    which of the twelve went wrong.
    """
    require_course(db, user, course_id, COORDINATOR)
    if not files:
        raise HTTPException(400, "no files were sent")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"at most {MAX_FILES} files at a time")

    jobs: list[IngestionJob] = []
    queued: list[UUID] = []
    for file in files:
        name = file.filename or "unnamed"
        job = IngestionJob(course_id=course_id, filename=name, state="queued")
        db.add(job)
        jobs.append(job)

        ext = "." + name.rsplit(".", 1)[-1].lower()
        if ext not in _SOURCE_TYPES:
            job.state, job.detail = "rejected", "only .pdf and .docx are accepted"
            job.finished_at = datetime.now(timezone.utc)
            continue

        dest, digest = _store(course_id, file, ext)
        twin = db.scalar(
            select(Material).where(
                Material.course_id == course_id, Material.content_hash == digest
            )
        )
        if twin is not None:
            dest.unlink(missing_ok=True)
            job.state = "duplicate"
            job.detail = f"the same file is already indexed as {twin.filename}"
            job.material_id = twin.material_id
            job.finished_at = datetime.now(timezone.utc)
            continue

        material = Material(
            course_id=course_id, filename=name, source_type=_SOURCE_TYPES[ext],
            stored_path=str(dest), content_hash=digest,
        )
        db.add(material)
        db.flush()
        job.material_id = material.material_id

    db.commit()
    for job in jobs:
        db.refresh(job)
        if job.state == "queued":
            queued.append(job.job_id)
    if queued:
        background.add_task(run_jobs, queued)
    return jobs


@router.get("/courses/{course_id}/ingestion-jobs", response_model=list[IngestionJobOut])
def list_jobs(course_id: UUID, limit: int = 25, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    """The upload queue, newest first. The screen polls this while anything is still running."""
    require_course(db, user, course_id)
    return db.scalars(
        select(IngestionJob).where(IngestionJob.course_id == course_id)
        .order_by(IngestionJob.created_at.desc()).limit(limit)
    ).all()


@router.get("/courses/{course_id}/materials", response_model=list[MaterialOut])
def list_materials(course_id: UUID, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    require_course(db, user, course_id)
    rows = db.scalars(select(Material).where(Material.course_id == course_id).order_by(Material.uploaded_at)).all()
    return [_material_out(db, m) for m in rows]


@router.get("/materials/{material_id}/chunks", response_model=list[ChunkOut])
def preview_chunks(material_id: UUID, limit: int = 5, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    """A few indexed passages, so ingestion is visibly real (plan §7.1 step 3)."""
    require_material(db, user, material_id)
    return db.scalars(select(Chunk).where(Chunk.material_id == material_id).order_by(Chunk.page_ref).limit(limit)).all()
