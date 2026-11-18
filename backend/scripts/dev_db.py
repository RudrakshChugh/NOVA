"""A local PostgreSQL with pgvector, with nothing to install system-wide.

    pip install pixeltable-pgserver        # once; development only, not in requirements.txt
    python scripts/dev_db.py               # start (or reuse) the server and print the URLs
    python scripts/dev_db.py --stop        # stop it

The project needs the `vector` extension, which a stock Postgres install does not have and which
on macOS means building it from source. `pixeltable-pgserver` ships Postgres binaries with
pgvector already in them as a Python wheel, so the whole database lives in this virtualenv and in
`backend/.localdb/` and is removed by deleting those two things.

The server keeps running after this script exits, so the app and the tests can both use it. It
listens on a Unix socket only, never on a network port.
"""

import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / ".localdb"
DATABASES = ("aqp", "aqp_test")


def main() -> int:
    try:
        import pixeltable_pgserver as pgserver
    except ImportError:
        print("pixeltable-pgserver is not installed. Run: pip install pixeltable-pgserver")
        return 1

    version = pgserver.TARGET_POSTGRES_VERSION
    if "--stop" in sys.argv:
        # cleanup_mode="stop" makes the server shut down when this handle is cleaned up.
        pgserver.get_server(DATA, cleanup_mode="stop", postgres_version=version).cleanup()
        print("stopped")
        return 0

    server = pgserver.get_server(DATA, cleanup_mode=None, postgres_version=version)
    existing = server.psql("SELECT datname FROM pg_database")
    for name in DATABASES:
        if name not in existing:
            server.psql(f'CREATE DATABASE "{name}"')

    def url(db: str) -> str:
        return server.get_uri(database=db).replace("postgresql://", "postgresql+psycopg://", 1)

    print("PostgreSQL with pgvector is running.\n")
    print("Put this in backend/.env to run the app against it:")
    print(f"  DATABASE_URL={url('aqp')}\n")
    print("Run the test suite against it:")
    print(f"  DATABASE_URL='{url('aqp_test')}' python -m pytest -q")
    return 0


if __name__ == "__main__":
    sys.exit(main())
