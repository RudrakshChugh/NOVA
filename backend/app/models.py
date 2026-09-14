"""ORM tables. Mirrors plan.md §8 (prototype tables) plus `users`, which
review_records.reviewer_id references."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)  # coordinator | reviewer


class Course(Base):
    __tablename__ = "courses"
    course_id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)


class Material(Base):
    __tablename__ = "materials"
    material_id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.course_id"))
    filename: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text)  # pdf | docx
    stored_path: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="material")


class Chunk(Base):
    __tablename__ = "chunks"
    chunk_id: Mapped[uuid.UUID] = _uuid_pk()
    material_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("materials.material_id"))
    text: Mapped[str] = mapped_column(Text)
    page_ref: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(384))
    material: Mapped[Material] = relationship(back_populates="chunks")


class Blueprint(Base):
    __tablename__ = "blueprints"
    blueprint_id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.course_id"))
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # assigned at freeze
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    constraints: Mapped[list["BlueprintConstraint"]] = relationship(
        back_populates="blueprint", cascade="all, delete-orphan", order_by="BlueprintConstraint.position"
    )


class BlueprintConstraint(Base):
    __tablename__ = "blueprint_constraints"
    constraint_id: Mapped[uuid.UUID] = _uuid_pk()
    blueprint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("blueprints.blueprint_id"))
    position: Mapped[int] = mapped_column(Integer)
    course_outcome: Mapped[str] = mapped_column(Text)
    concept: Mapped[str] = mapped_column(Text)
    bloom_level: Mapped[str] = mapped_column(Text)
    marks: Mapped[int] = mapped_column(Integer)
    question_type: Mapped[str] = mapped_column(Text)
    target_difficulty: Mapped[str] = mapped_column(Text)
    blueprint: Mapped[Blueprint] = relationship(back_populates="constraints")


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    run_id: Mapped[uuid.UUID] = _uuid_pk()
    blueprint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("blueprints.blueprint_id"))
    backend_id: Mapped[str] = mapped_column(Text)
    prompt_hash: Mapped[str] = mapped_column(Text)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(Text)  # running | completed | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    blueprint: Mapped[Blueprint] = relationship()
    candidates: Mapped[list["CandidateQuestion"]] = relationship(back_populates="run")


class CandidateQuestion(Base):
    __tablename__ = "candidate_questions"
    question_id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_runs.run_id"))
    constraint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("blueprint_constraints.constraint_id"))
    arm_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    stem: Mapped[str] = mapped_column(Text)
    answer_key: Mapped[str] = mapped_column(Text)
    options: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # MCQ options, else null
    state: Mapped[str] = mapped_column(Text)
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0)
    run: Mapped[GenerationRun] = relationship(back_populates="candidates")
    constraint: Mapped[BlueprintConstraint] = relationship()
    citations: Mapped[list["GroundingCitation"]] = relationship(back_populates="question")
    reports: Mapped[list["ComplianceReport"]] = relationship(
        back_populates="question", order_by="ComplianceReport.created_at"
    )
    reviews: Mapped[list["ReviewRecord"]] = relationship(back_populates="question")


class GroundingCitation(Base):
    __tablename__ = "grounding_citations"
    citation_id: Mapped[uuid.UUID] = _uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_questions.question_id"))
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.chunk_id"))
    char_span: Mapped[str] = mapped_column(Text)
    support_score: Mapped[float] = mapped_column(Float)
    question: Mapped[CandidateQuestion] = relationship(back_populates="citations")
    chunk: Mapped[Chunk] = relationship()


class ComplianceReport(Base):
    __tablename__ = "compliance_reports"
    report_id: Mapped[uuid.UUID] = _uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_questions.question_id"))
    is_compliant: Mapped[bool] = mapped_column(Boolean)
    satisfaction_rate: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    question: Mapped[CandidateQuestion] = relationship(back_populates="reports")
    violations: Mapped[list["ConstraintViolation"]] = relationship(back_populates="report")


class ConstraintViolation(Base):
    __tablename__ = "constraint_violations"
    violation_id: Mapped[uuid.UUID] = _uuid_pk()
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("compliance_reports.report_id"))
    field: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)
    observed: Mapped[str] = mapped_column(Text)
    report: Mapped[ComplianceReport] = relationship(back_populates="violations")


class ReviewRecord(Base):
    __tablename__ = "review_records"
    record_id: Mapped[uuid.UUID] = _uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_questions.question_id"))
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"))
    decision: Mapped[str] = mapped_column(Text)  # accept | edit | reject
    edited_stem: Mapped[str | None] = mapped_column(Text, nullable=True)
    edit_distance: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    blinded: Mapped[bool] = mapped_column(Boolean, default=False)
    question: Mapped[CandidateQuestion] = relationship(back_populates="reviews")
    reviewer: Mapped[User] = relationship()


class QuestionBankEntry(Base):
    __tablename__ = "question_bank"
    entry_id: Mapped[uuid.UUID] = _uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_questions.question_id"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
