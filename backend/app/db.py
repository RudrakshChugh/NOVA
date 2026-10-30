from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

# `pool_pre_ping` is not optional against a serverless Postgres that scales to zero: the server
# drops idle connections when it suspends, so a pooled connection that looked fine at checkout is
# already dead. Pre-ping discards it and opens a new one, which also wakes the compute. `recycle`
# keeps a connection from outliving an idle-timeout window in the first place.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# Columns added to tables that already exist in a deployed database. `create_all` creates missing
# tables but never alters existing ones, so each change is listed here as an idempotent statement
# and applied at startup. This stands in for a migration tool while the schema is small; once the
# evaluation data matters, move these into Alembic so the history is versioned rather than listed.
SCHEMA_PATCHES = (
    "ALTER TABLE generation_runs ADD COLUMN IF NOT EXISTS arm_label TEXT",
)


def init_db() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for statement in SCHEMA_PATCHES:
            conn.execute(text(statement))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
