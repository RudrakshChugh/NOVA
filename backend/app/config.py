from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/aqp"
    storage_dir: Path = Path("../storage")
    session_secret: str = "change-me"
    frontend_url: str = "http://localhost:5173"  # where the callback sends the browser back to

    # Auth: OIDC when configured (plan §4); DEV_LOGIN allows seeded users to log in without an IdP.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str = "http://localhost:8000/auth/callback"
    dev_login: bool = True

    # The first subject, seeded at startup so a fresh deployment is never an empty screen with
    # no way forward. Further subjects are created in the app by an administrator (plan.md §14.1).
    course_code: str = "CS101"
    course_title: str = "Prototype Course"

    # Institutional restriction. Google's `hd` parameter only filters its own account picker, so
    # the domain is re-checked server-side on the claims that come back.
    allowed_domain: str | None = None  # e.g. thapar.edu
    allowed_emails: str = ""  # comma-separated exceptions to the domain rule
    # These addresses are admitted as platform administrators. Everyone else arrives as
    # `pending`, is admitted by an administrator, and is then put on a subject by its coordinator.
    coordinator_emails: str = ""

    @property
    def coordinators(self) -> set[str]:
        return {e.strip().lower() for e in self.coordinator_emails.split(",") if e.strip()}

    @property
    def email_exceptions(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    @property
    def allowed_origins(self) -> list[str]:
        return list({"http://localhost:5173", self.frontend_url.rstrip("/")})

    # Ingestion
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # 384 dims, matches chunks.embedding VECTOR(384)
    chunk_window_sentences: int = 5
    chunk_stride_sentences: int = 3

    # Generation
    llm_backend: str = "ollama"  # ollama | anthropic | groq | failover
    ollama_model: str = "qwen3:8b"
    ollama_url: str = "http://localhost:11434"
    ollama_timeout: float = 900.0  # CPU inference; one draft can take minutes
    anthropic_model: str = "claude-opus-5"

    # Hosted fallback, used when the self-hosted machine is unreachable.
    groq_api_key: str | None = None
    # Groq retires models on a schedule, so this will go stale again. A model error reports
    # what the key can actually reach, and this is overridable without a code change.
    groq_model: str = "openai/gpt-oss-120b"
    groq_url: str = "https://api.groq.com/openai/v1"
    groq_timeout: float = 120.0
    groq_max_retries: int = 4  # a free tier's per-minute limit is met by any run of size
    groq_max_backoff: float = 30.0
    # Cloudflare Access sits in front of the tunnel, so Ollama is never openly exposed.
    cf_access_client_id: str | None = None
    cf_access_client_secret: str | None = None
    failover_probe_timeout: float = 2.0
    generation_temperature: float = 0.7  # plan §3.3.2; logged per run, honoured only by backends that accept it
    retrieval_top_k: int = 5
    # Below this cosine similarity the best passage found for a blueprint row is about something
    # else: the material does not cover the row's concept. Retrieval always returns its top
    # passages however weak they are, so without a floor the model is handed unrelated text and
    # asked to write a "grounded" question from it — which it does, by inventing the content.
    # Set from two measurements rather than chosen: on real lecture slides, rows the slides
    # cover scored 0.57-0.70 and rows they do not scored 0.30-0.37; on a short mixed-topic
    # passage, on-topic rows scored as low as 0.44. 0.40 sits between the two. It is an
    # embedding similarity, so it is a judgement near the line — a row that is wrongly skipped
    # says so by name on the run, and this can be lowered. 0 disables the check.
    # Normal runs only; evaluation arms are never filtered.
    retrieval_min_similarity: float = 0.40
    repair_budget: int = 2
    # Extra attempts when a reply cannot be read at all (not JSON, no question in it). Separate
    # from the repair budget: that one is for a question that is wrong, this for a reply that is
    # not a question.
    malformed_retry_budget: int = 2
    # After a question is drafted, ask the model which sentences of the material state each
    # fact in its answer key, then verify those quotes verbatim (services/claims.py). One extra
    # model call per question, normal runs only, and it gates nothing.
    claim_check_enabled: bool = True
    # Rewrites allowed when the evidence check finds statements the passages do not make.
    evidence_redraft_budget: int = 1
    grounding_min_similarity: float = 0.25

    # Novelty engine
    novelty_enabled: bool = True
    novelty_shortlist: int = 10  # past questions scored on all four signals per candidate
    novelty_redraft_budget: int = 1  # redrafts allowed when a draft duplicates a past question
    # Past-year questions shown to the model before it drafts, so the first draft can avoid them.
    # The floor is a cosine similarity to the blueprint row: below it a past question is about
    # something else and listing it would only be noise.
    novelty_avoid_k: int = 3
    novelty_avoid_min_similarity: float = 0.35
    # Also audit a draft against this run's earlier questions and the published bank, and show
    # the model up to this many of the run's related questions. Normal runs only; an evaluation
    # run is measured against past papers alone.
    novelty_internal_check: bool = True
    sibling_prompt_limit: int = 5
    novelty_model_path: Path = Path(__file__).resolve().parents[1] / "data" / "models" / "novelty_combiner.json"

    # Reviewer-adapted ranker
    source_grounded_max_chunks: int = 80  # cap for the whole-material baseline's prompt

    ranker_model_path: Path = Path(__file__).resolve().parents[1] / "data" / "models" / "ranker.json"


    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, url: str) -> str:
        """Hosted Postgres providers all hand out `postgresql://` (or the older `postgres://`), and
        SQLAlchemy maps both to psycopg2 — a driver this project deliberately does not install.
        Left alone it fails at import time with ModuleNotFoundError, which names psycopg2 and so
        points away from the actual mistake. Normalising here means a connection string can be
        pasted exactly as the provider gives it."""
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url


settings = Settings()
