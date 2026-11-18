"""Readiness report for one subject: can a generation run started now actually succeed?

A run takes minutes and fails late. The commonest reasons are all knowable before it starts —
the model backend is switched off, the embedding model was never downloaded, nothing has been
indexed for the subject — so the Generate screen asks here first and says which it is.
"""

from uuid import UUID

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..auth import current_user
from ..config import settings
from ..db import get_db
from ..llm.base import access_headers
from ..models import PYQ, Chunk, CourseOutcome, Material, PYQPaper, QuestionBankEntry, User
from ..models import Blueprint, BlueprintConstraint, CandidateQuestion
from ..scoping import require_course
from ..services import embeddings, novelty

router = APIRouter(tags=["system"])


class CheckItem(BaseModel):
    key: str
    label: str
    # ok: ready. warn: a run will work but something it could use is missing. bad: a run
    # started now will fail or produce nothing usable.
    state: str
    detail: str


class StatusOut(BaseModel):
    ready: bool  # no check is `bad`
    backend: str
    checks: list[CheckItem]


def _llm_check() -> CheckItem:
    backend = settings.llm_backend
    label = "Language model"
    if backend in ("ollama", "failover"):
        url = (settings.ollama_url or "").rstrip("/")
        try:
            r = httpx.get(f"{url}/api/tags", headers=access_headers(), timeout=settings.failover_probe_timeout)
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
        except Exception as e:  # any failure at all means "not usable right now"
            if backend == "failover" and settings.groq_api_key:
                return CheckItem(key="llm", label=label, state="ok",
                                 detail=f"Self-hosted model is not reachable; runs will use Groq ({settings.groq_model}).")
            return CheckItem(key="llm", label=label, state="bad",
                             detail=f"Ollama is not reachable at {url} ({type(e).__name__}). Start it, or set LLM_BACKEND.")
        wanted = settings.ollama_model
        if not any(n == wanted or n.split(":")[0] == wanted for n in names):
            return CheckItem(key="llm", label=label, state="bad",
                             detail=f"Ollama is running but has no model named {wanted}. Run: ollama pull {wanted}")
        return CheckItem(key="llm", label=label, state="ok", detail=f"Ollama · {wanted}")
    if backend == "groq":
        if not settings.groq_api_key:
            return CheckItem(key="llm", label=label, state="bad", detail="GROQ_API_KEY is not set.")
        return CheckItem(key="llm", label=label, state="ok", detail=f"Groq · {settings.groq_model} (key present, not probed)")
    if backend == "anthropic":
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            return CheckItem(key="llm", label=label, state="bad", detail="ANTHROPIC_API_KEY is not set.")
        return CheckItem(key="llm", label=label, state="ok",
                         detail=f"Anthropic · {settings.anthropic_model} (key present, not probed)")
    return CheckItem(key="llm", label=label, state="bad", detail=f"Unknown LLM_BACKEND: {backend}")


@router.get("/courses/{course_id}/status", response_model=StatusOut)
def status(course_id: UUID, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_course(db, user, course_id)
    checks: list[CheckItem] = []

    try:
        db.execute(text("SELECT 1"))
        checks.append(CheckItem(key="database", label="Database", state="ok", detail="PostgreSQL with pgvector"))
    except Exception as e:
        checks.append(CheckItem(key="database", label="Database", state="bad", detail=f"{type(e).__name__}"))

    if embeddings.MODEL_PATH.exists() and embeddings.TOKENIZER_PATH.exists():
        checks.append(CheckItem(key="embeddings", label="Embedding model", state="ok", detail=embeddings.model_name()))
    else:
        checks.append(CheckItem(key="embeddings", label="Embedding model", state="bad",
                                detail="Not downloaded. Run: python scripts/fetch_embedding_model.py"))

    checks.append(_llm_check())

    passages = db.scalar(
        select(func.count(Chunk.chunk_id)).join(Material, Material.material_id == Chunk.material_id)
        .where(Material.course_id == course_id)
    ) or 0
    checks.append(CheckItem(
        key="material", label="Course material", state="ok" if passages else "bad",
        detail=f"{passages} indexed passage(s)" if passages
        else "Nothing indexed. Every draft would fail the grounding check — upload material first.",
    ))

    outcomes = db.scalar(select(func.count(CourseOutcome.outcome_id)).where(CourseOutcome.course_id == course_id)) or 0
    checks.append(CheckItem(
        key="outcomes", label="Course outcomes", state="ok" if outcomes else "warn",
        detail=f"{outcomes} outcome(s) on record" if outcomes
        else "None on record, so blueprint rows carry typed outcome text and coverage cannot be counted.",
    ))

    past = db.scalar(
        select(func.count(PYQ.pyq_id)).join(PYQPaper, PYQPaper.paper_id == PYQ.paper_id)
        .where(PYQPaper.course_id == course_id)
    ) or 0
    banked = db.scalar(
        select(func.count(QuestionBankEntry.entry_id))
        .join(CandidateQuestion, CandidateQuestion.question_id == QuestionBankEntry.question_id)
        .join(BlueprintConstraint, BlueprintConstraint.constraint_id == CandidateQuestion.constraint_id)
        .join(Blueprint, Blueprint.blueprint_id == BlueprintConstraint.blueprint_id)
        .where(Blueprint.course_id == course_id)
    ) or 0
    if not settings.novelty_enabled:
        checks.append(CheckItem(key="novelty", label="Novelty audit", state="warn", detail="Switched off (NOVELTY_ENABLED=false)."))
    else:
        fitted = novelty.load_combiner(course_id).is_fitted
        parts = [f"{past} past-year question(s)", f"{banked} in the bank"]
        parts.append("fitted combiner" if fitted else "combiner not fitted (equal-weight placeholder)")
        checks.append(CheckItem(
            key="novelty", label="Novelty audit",
            state="ok" if past and fitted else "warn",
            detail=" · ".join(parts) + ("" if past else ". Add past papers so drafts can be checked against them."),
        ))

    return StatusOut(ready=all(c.state != "bad" for c in checks), backend=settings.llm_backend, checks=checks)
