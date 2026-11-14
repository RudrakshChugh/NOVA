from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

BLOOM_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate", "create")
QUESTION_TYPES = ("mcq", "short_answer", "long_answer", "numerical")


class UserOut(BaseModel):
    user_id: UUID
    email: str
    name: str
    role: str


class DevLoginIn(BaseModel):
    email: str


class RoleIn(BaseModel):
    role: str


class PersonOut(UserOut):
    """A user plus how they came to hold the platform standing they hold."""

    role_set_by: str | None = None  # null means configuration, not a person
    role_set_at: datetime | None = None
    role_reason: str | None = None


class TokenOut(BaseModel):
    token: str
    user: UserOut


class CourseOut(BaseModel):
    course_id: UUID
    code: str
    title: str
    # The caller’s role on this subject. The interface gates every control on it, so sending
    # it with the subject saves a request per screen and keeps the two from disagreeing.
    your_role: str | None = None


class CourseIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=200)


class MemberIn(BaseModel):
    user_id: UUID
    role: str  # reviewer | coordinator
    reason: str | None = None


class CourseMemberOut(BaseModel):
    """A person on one subject. Platform standing is separate and lives on PersonOut."""

    user_id: UUID
    email: str
    name: str
    role: str
    granted_at: datetime
    reason: str | None = None


class MaterialOut(BaseModel):
    material_id: UUID
    filename: str
    source_type: str
    uploaded_at: datetime
    chunk_count: int
    pages_covered: int  # distinct page refs (pdf pages / docx paragraph windows) with at least one chunk


class IngestionJobOut(BaseModel):
    """One file’s progress. A queue of these is what the upload screen shows."""

    job_id: UUID
    filename: str
    state: str  # queued | running | done | failed | duplicate | rejected
    detail: str | None = None
    chunk_count: int
    material_id: UUID | None = None
    created_at: datetime
    finished_at: datetime | None = None


class ChunkOut(BaseModel):
    chunk_id: UUID
    page_ref: str
    text: str


class OutcomeIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    text: str = Field(min_length=1)
    confidence: float | None = None


class OutcomeOut(OutcomeIn):
    outcome_id: UUID
    position: int
    source_filename: str | None = None


class ExtractedOutcomeOut(BaseModel):
    """What a syllabus appears to say. Nothing is stored until the coordinator saves it."""

    code: str
    text: str
    confidence: float


class SyllabusReadOut(BaseModel):
    filename: str
    outcomes: list[ExtractedOutcomeOut]
    note: str


class ConstraintIn(BaseModel):
    course_outcome: str = Field(min_length=1)
    outcome_id: UUID | None = None
    concept: str = Field(min_length=1)
    bloom_level: Literal[BLOOM_LEVELS]
    marks: int = Field(ge=1)
    question_type: Literal[QUESTION_TYPES]


class ConstraintOut(ConstraintIn):
    constraint_id: UUID
    position: int


class BlueprintOut(BaseModel):
    blueprint_id: UUID
    course_id: UUID
    version: int | None
    revised_from: UUID | None = None
    revised_from_version: int | None = None
    created_at: datetime | None = None
    frozen_at: datetime | None
    constraints: list[ConstraintOut]


class ValidationOut(BaseModel):
    ok: bool
    issues: list[str]
    total_marks: int
    outcomes: dict[str, int]  # course outcome -> marks allocated
    # Outcomes of the subject with no marks allocated to them. A gap, not an error: a single
    # paper is not obliged to cover every outcome, but the coordinator should see which it
    # leaves out rather than discover it afterwards.
    uncovered: list[str] = []


class GenerateIn(BaseModel):
    """What the reviewer is after, in their own words. Optional, and empty by default."""

    suggestion: str | None = Field(default=None, max_length=1000)


class RunOut(BaseModel):
    run_id: UUID
    blueprint_id: UUID
    backend_id: str
    prompt_hash: str
    suggestion: str | None = None
    temperature: float | None
    seed: int | None
    started_at: datetime
    status: str
    error: str | None
    candidate_count: int
    constraint_count: int
    arm_label: str | None


class CitationOut(BaseModel):
    citation_id: UUID
    chunk_id: UUID
    page_ref: str
    text: str
    char_span: str
    support_score: float


class ViolationOut(BaseModel):
    field: str
    expected: str
    observed: str


class CheckOut(BaseModel):
    field: str
    passed: bool
    expected: str | None  # populated when the check failed
    observed: str | None


class ComplianceOut(BaseModel):
    report_id: UUID
    is_compliant: bool
    satisfaction_rate: float
    violations: list[ViolationOut]
    checks: list[CheckOut]  # every check the engine ran on this draft, pass or fail


class ReviewOut(BaseModel):
    record_id: UUID
    reviewer: UserOut
    decision: str
    edited_stem: str | None
    edit_distance: float | None
    reason: str | None
    reviewed_at: datetime


class CandidateOut(BaseModel):
    question_id: UUID
    run_id: UUID | None  # withheld in the blinded review queue, which must not identify the arm
    constraint: ConstraintOut
    stem: str
    answer_key: str
    options: list[str] | None
    state: str
    repair_attempts: int
    citations: list[CitationOut]
    compliance: ComplianceOut | None  # latest report
    novelty: "NoveltyOut | None"  # latest novelty audit, null when the engine did not run
    review: ReviewOut | None  # latest review


class EvidenceOut(BaseModel):
    question_id: UUID
    citations: list[CitationOut]
    reports: list[ComplianceOut]  # every report, first draft through repairs


class ReviewIn(BaseModel):
    decision: Literal["accept", "edit", "reject"]
    edited_stem: str | None = None
    reason: str | None = None


class ReviewSummaryOut(BaseModel):
    run_id: UUID
    total: int
    accepted: int
    edited: int
    rejected: int
    pending: int
    mean_edit_distance: float | None


class StudyLaunchIn(BaseModel):
    arms: list[str] = Field(min_length=1)
    seeds: int = Field(default=3, ge=1, le=10)  # plan §3.3.3 runs each condition three times


class PublishIn(BaseModel):
    question_ids: list[UUID]


class BankEntryOut(BaseModel):
    entry_id: UUID
    question_id: UUID
    version: int
    snapshot: dict
    published_at: datetime


# --- Novelty engine ---


class PYQPasteIn(BaseModel):
    year: int = Field(ge=1990, le=2100)
    label: str = Field(min_length=1)
    text: str = Field(min_length=1)


class PYQOut(BaseModel):
    pyq_id: UUID
    number: str | None
    text: str
    marks: int | None
    question_type: str | None
    bloom_level: str | None


class PYQPaperOut(BaseModel):
    paper_id: UUID
    year: int
    detected_year: int | None = None
    label: str
    filename: str | None
    source_type: str
    uploaded_at: datetime
    question_count: int


class NoveltySignalOut(BaseModel):
    name: str
    value: float
    weight: float | None  # combiner coefficient, null while the combiner is unfitted


class NoveltyOut(BaseModel):
    report_id: UUID
    duplicate_probability: float
    threshold: float
    is_duplicate: bool
    model_version: str
    signals: list[NoveltySignalOut]
    nearest_pyq: PYQOut | None
    nearest_paper: str | None  # "End Semester 2024"


class PairBuildIn(BaseModel):
    positives_per_question: int = Field(default=2, ge=1, le=6)
    hard_negatives_per_question: int = Field(default=1, ge=0, le=5)
    easy_negatives_per_question: int = Field(default=1, ge=0, le=5)


class DuplicatePairOut(BaseModel):
    pair_id: UUID
    pair_key: str
    left_text: str
    right_text: str
    label: bool
    stratum: str
    provenance: str
    split: str


CandidateOut.model_rebuild()  # resolves the forward reference to NoveltyOut


# --- Paper assembly (plan.md §14.3) ---


class PaperTemplateIn(BaseModel):
    institution: str = ""
    department: str = ""
    exam_name: str = ""
    duration_minutes: int | None = Field(default=None, ge=1, le=600)
    instructions: str = ""


class PaperTemplateOut(PaperTemplateIn):
    template_id: UUID | None = None


class PaperCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    blueprint_id: UUID | None = None  # fills the paper from that blueprint's published questions
    exam_date: str | None = None


class PaperItemIn(BaseModel):
    entry_id: UUID
    number: int = Field(ge=1)
    part: str | None = Field(default=None, max_length=2)
    marks: int = Field(ge=1)


class PaperItemOut(PaperItemIn):
    item_id: UUID
    position: int
    stem: str
    options: list[str] | None = None
    answer_key: str
    course_outcome: str
    bloom_level: str


class PaperOut(BaseModel):
    paper_id: UUID
    course_id: UUID
    blueprint_id: UUID | None
    title: str
    exam_date: str | None
    state: str
    created_at: datetime
    finalised_at: datetime | None
    total_marks: int
    items: list[PaperItemOut]


class PaperCheckOut(BaseModel):
    """Whether the paper adds up to the blueprint it was built from."""

    ok: bool
    issues: list[str]
    total_marks: int
    blueprint_marks: int | None
