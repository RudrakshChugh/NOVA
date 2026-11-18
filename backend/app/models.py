"""ORM tables. Mirrors plan.md §8 plus `users` (referenced by review_records.reviewer_id)
and the novelty-engine tables the full system needs."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Text, UniqueConstraint, func
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
    # Platform standing only: pending | member | admin (plan.md 14.1). What somebody may do to a
    # subject lives in `course_members`, because the institute runs many subjects with a
    # different coordinator for each. `pending` satisfies no check anywhere: the account is
    # authenticated and nothing else. Review decisions are the ranker's training data and the
    # input to the inter-rater statistic, so an unadmitted account must not produce any.
    role: Mapped[str] = mapped_column(Text, default="pending")


class RoleChange(Base):
    """Every role a user has ever held, and who put them there.

    Without this, "was that account made a coordinator deliberately or by configuration?" is
    unanswerable from the data — the only record is the role itself, which looks identical either
    way. Granting access is the one action here that widens what somebody can do, so it is the one
    that most needs a trail.
    """

    __tablename__ = "role_changes"
    change_id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"))
    # Null when no person did it: the role came from configuration at first sign-in.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.user_id"), nullable=True)
    from_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_role: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Course(Base):
    __tablename__ = "courses"
    course_id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)


class CourseMember(Base):
    """Who may do what on one subject.

    Scoping is what makes a missing filter a disclosure between colleagues rather than a
    cosmetic bug, so membership is a row that can be read, audited and revoked rather than a
    value inferred from anywhere else.
    """

    __tablename__ = "course_members"
    membership_id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.course_id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"))
    role: Mapped[str] = mapped_column(Text)  # reviewer | coordinator
    # Null when no person did it: the membership came from configuration or from the one-time
    # translation of the platform-wide roles that preceded this table.
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.user_id"), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("course_id", "user_id", name="uq_course_member"),)
    course: Mapped["Course"] = relationship()
    user: Mapped["User"] = relationship(foreign_keys=[user_id])


class CourseOutcome(Base):
    """One stated outcome of a subject (plan.md §14.6).

    A row rather than free text on every constraint, so that the same outcome cannot be spelt
    three ways in one blueprint and the coverage map validation returns means something.
    """

    __tablename__ = "course_outcomes"
    outcome_id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.course_id"))
    code: Mapped[str] = mapped_column(Text)  # CO1, CO2, ...
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    # Where it came from, and how sure the extractor was. Kept after editing: a coordinator
    # reading the list later should be able to see which rows were read off a document and
    # which were typed.
    source_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Material(Base):
    __tablename__ = "materials"
    material_id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.course_id"))
    filename: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text)  # pdf | docx
    stored_path: Mapped[str] = mapped_column(Text)
    # SHA-256 of the bytes. Re-uploading an overlapping folder is the normal case, not the
    # exception, and embedding the same passages twice inflates the retrieval index with
    # duplicates that then crowd out everything else. Nullable for rows stored before this.
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="material")


class IngestionJob(Base):
    """One file being parsed, chunked and embedded (plan.md §14.2).

    Embedding is the slow step, the free tier has 512 MB, and a long synchronous request is
    the thing a platform is most likely to cut off. So the upload returns as soon as the bytes
    are safe and the work happens behind a row the screen can poll — which also means a
    coordinator can close the tab on a folder of twelve lecture decks.
    """

    __tablename__ = "ingestion_jobs"
    job_id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.course_id"))
    material_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("materials.material_id"), nullable=True
    )
    filename: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)  # queued | running | done | failed | duplicate | rejected
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    # The frozen version this draft was copied from (plan.md §14.5). A frozen blueprint is
    # never edited in place — runs, candidates and compliance reports all reference its
    # constraint ids, and the evaluation claims rest on those still meaning what they meant at
    # generation time — so revising one copies it and records the descent here.
    revised_from: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blueprints.blueprint_id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
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
    # The outcome row this points at, once the subject has them. Nullable, because a blueprint
    # written before the subject’s outcomes were extracted still has to load (plan.md §14.6).
    outcome_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course_outcomes.outcome_id"), nullable=True
    )
    concept: Mapped[str] = mapped_column(Text)
    bloom_level: Mapped[str] = mapped_column(Text)
    marks: Mapped[int] = mapped_column(Integer)
    question_type: Mapped[str] = mapped_column(Text)
    blueprint: Mapped[Blueprint] = relationship(back_populates="constraints")


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    run_id: Mapped[uuid.UUID] = _uuid_pk()
    blueprint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("blueprints.blueprint_id"))
    backend_id: Mapped[str] = mapped_column(Text)
    prompt_hash: Mapped[str] = mapped_column(Text)
    # What the reviewer asked for in their own words, if anything (plan.md §14.4). It is part
    # of prompt_hash, so two runs that differ only in this are not mistaken for the same
    # configuration — and it is queried directly to keep hinted runs out of the evaluation,
    # where a hint is a confound rather than a feature.
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(Text)  # running | completed | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Null for normal platform use; the baseline or ablation name during an evaluation run. Plan
    # §8 puts this on candidates, which inherit it; a run executes exactly one arm, so it is
    # recorded here too and the harness can group by it without joining.
    arm_label: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # The evidence report for the answer key (services/claims.py), or null when it was not
    # made: an evaluation run, a model that returned nothing usable, or a row written before
    # this existed.
    claim_check: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    run: Mapped[GenerationRun] = relationship(back_populates="candidates")
    constraint: Mapped[BlueprintConstraint] = relationship()
    citations: Mapped[list["GroundingCitation"]] = relationship(back_populates="question")
    reports: Mapped[list["ComplianceReport"]] = relationship(
        back_populates="question", order_by="ComplianceReport.created_at"
    )
    novelty_reports: Mapped[list["NoveltyReport"]] = relationship(
        back_populates="question", order_by="NoveltyReport.created_at"
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
    question: Mapped["CandidateQuestion"] = relationship()


# --- Novelty engine (plan §3.1): past-year corpus, audit reports, labelled pair set ---


class PYQPaper(Base):
    """One uploaded past-year question paper."""

    __tablename__ = "pyq_papers"
    paper_id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.course_id"))
    year: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)  # e.g. "End Semester 2024"
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)  # null when pasted as text
    source_type: Mapped[str] = mapped_column(Text)  # pdf | docx | text
    # The year printed on the paper itself, when it states one. Advisory: it is shown next to
    # the year that was entered so a disagreement is visible rather than silent.
    detected_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stored_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    questions: Mapped[list["PYQ"]] = relationship(back_populates="paper", cascade="all, delete-orphan")


class PYQ(Base):
    """One historical question, with the metadata the structural signal compares."""

    __tablename__ = "pyqs"
    pyq_id: Mapped[uuid.UUID] = _uuid_pk()
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pyq_papers.paper_id"))
    number: Mapped[str | None] = mapped_column(Text, nullable=True)  # "1", "2b", ...
    text: Mapped[str] = mapped_column(Text)
    marks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    bloom_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding = mapped_column(Vector(384))
    paper: Mapped[PYQPaper] = relationship(back_populates="questions")


class NoveltyReport(Base):
    """Novelty audit of one candidate draft against the PYQ corpus."""

    __tablename__ = "novelty_reports"
    report_id: Mapped[uuid.UUID] = _uuid_pk()
    question_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_questions.question_id"))
    nearest_pyq_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pyqs.pyq_id"), nullable=True)
    duplicate_probability: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    is_duplicate: Mapped[bool] = mapped_column(Boolean)
    model_version: Mapped[str] = mapped_column(Text)  # combiner identity, or "untrained" fallback
    # What the nearest question was, when it was not necessarily a past-year one. A draft can
    # also repeat an earlier candidate of its own run or a question already in the bank, and a
    # report that says "duplicate" has to be able to say of what. The text is copied rather than
    # referenced because a run candidate may be redrafted after this report was written.
    nearest_source: Mapped[str | None] = mapped_column(Text, nullable=True)  # pyq | run | bank
    nearest_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    nearest_label: Mapped[str | None] = mapped_column(Text, nullable=True)  # "Q3 of this run"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    question: Mapped["CandidateQuestion"] = relationship(back_populates="novelty_reports")
    nearest_pyq: Mapped[PYQ | None] = relationship()
    signals: Mapped[list["NoveltySignal"]] = relationship(back_populates="report", cascade="all, delete-orphan")


class NoveltySignal(Base):
    """Per-signal breakdown behind a novelty report (semantic, lexical, structural, numerical)."""

    __tablename__ = "novelty_signals"
    signal_id: Mapped[uuid.UUID] = _uuid_pk()
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("novelty_reports.report_id"))
    name: Mapped[str] = mapped_column(Text)
    value: Mapped[float] = mapped_column(Float)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)  # combiner coefficient, when trained
    report: Mapped[NoveltyReport] = relationship(back_populates="signals")


class DuplicatePair(Base):
    """Labelled question pair for fitting and evaluating the novelty combiner (plan §5)."""

    __tablename__ = "duplicate_pairs"
    pair_id: Mapped[uuid.UUID] = _uuid_pk()
    # The subject whose past-year corpus these two questions came from. Pairs are built from
    # one subject and the combiner fitted on them is used on that subject, so mixing them
    # would train one department’s duplicate detector on another’s papers. Nullable for rows
    # written before subjects existed.
    course_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("courses.course_id"), nullable=True)
    pair_key: Mapped[str] = mapped_column(Text, unique=True)  # stable hash of the two texts; split is keyed on this
    left_text: Mapped[str] = mapped_column(Text)
    right_text: Mapped[str] = mapped_column(Text)
    left_pyq_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pyqs.pyq_id"), nullable=True)
    right_pyq_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pyqs.pyq_id"), nullable=True)
    label: Mapped[bool] = mapped_column(Boolean)  # true = duplicate
    stratum: Mapped[str] = mapped_column(Text)  # surface | hard
    provenance: Mapped[str] = mapped_column(Text)  # transform name, "sampled", "hard_negative", "human"
    split: Mapped[str] = mapped_column(Text)  # train | val | test
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.user_id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelVersion(Base):
    """A fitted novelty combiner or ranker, with the metrics it was accepted on."""

    __tablename__ = "model_versions"
    model_id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("courses.course_id"), nullable=True)
    kind: Mapped[str] = mapped_column(Text)  # novelty | ranker
    algorithm: Mapped[str] = mapped_column(Text)  # logistic_regression | lightgbm | compliance_only
    version: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSONB)
    metrics: Mapped[dict] = mapped_column(JSONB)
    artifact_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Paper assembly (plan.md §14.3): published questions bound into a paper ---


class PaperTemplate(Base):
    """The institutional furniture of a paper's first page.

    Kept per subject and reused, because it is the part nobody wants to retype and the part a
    department is fussiest about.
    """

    __tablename__ = "paper_templates"
    template_id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.course_id"), unique=True)
    institution: Mapped[str] = mapped_column(Text, default="")
    department: Mapped[str] = mapped_column(Text, default="")
    exam_name: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instructions: Mapped[str] = mapped_column(Text, default="")
    print_mapping: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Paper(Base):
    """An ordered selection of published questions, bound into something printable.

    A stored object rather than a view, because ordering and grouping are decisions somebody makes
    and then wants back tomorrow. Finalising freezes a snapshot for the same reason a bank entry
    carries one: a paper that was printed must stay reproducible after its questions are revised.
    """

    __tablename__ = "papers"
    paper_id: Mapped[uuid.UUID] = _uuid_pk()
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.course_id"))
    blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blueprints.blueprint_id"), nullable=True
    )
    title: Mapped[str] = mapped_column(Text)
    exam_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, default="draft")  # draft | final
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finalised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    items: Mapped[list["PaperItem"]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", order_by="PaperItem.position"
    )


class PaperItem(Base):
    """One question on the paper, at its number and sub-part."""

    __tablename__ = "paper_items"
    item_id: Mapped[uuid.UUID] = _uuid_pk()
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.paper_id"))
    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("question_bank.entry_id"))
    position: Mapped[int] = mapped_column(Integer)
    number: Mapped[int] = mapped_column(Integer)
    part: Mapped[str | None] = mapped_column(Text, nullable=True)  # "a", "b", ... or null
    marks: Mapped[int] = mapped_column(Integer)
    paper: Mapped[Paper] = relationship(back_populates="items")
    entry: Mapped["QuestionBankEntry"] = relationship()
