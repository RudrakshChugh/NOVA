from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/aqp"
    storage_dir: Path = Path("../storage")
    session_secret: str = "change-me"
    cors_origins: list[str] = ["http://localhost:5173"]

    # Auth: OIDC when configured (plan §4); DEV_LOGIN allows seeded users to log in without an IdP.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str = "http://localhost:8000/auth/callback"
    dev_login: bool = True

    # Ingestion
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # 384 dims, matches chunks.embedding VECTOR(384)
    chunk_window_sentences: int = 5
    chunk_stride_sentences: int = 3

    # Generation
    llm_backend: str = "ollama"  # ollama | anthropic
    ollama_model: str = "qwen3:8b"
    ollama_url: str = "http://localhost:11434"
    ollama_timeout: float = 900.0  # CPU inference; one draft can take minutes
    anthropic_model: str = "claude-opus-5"
    generation_temperature: float = 0.7  # plan §3.3.2; logged per run, honoured only by backends that accept it
    retrieval_top_k: int = 5
    repair_budget: int = 2
    grounding_min_similarity: float = 0.25


settings = Settings()
