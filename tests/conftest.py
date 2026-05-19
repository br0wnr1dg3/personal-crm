import sqlite3
from pathlib import Path
import pytest

from netcrm import db, ingest

REPO_ROOT = Path(__file__).parent.parent
_FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def populated_db(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    ingest.ingest_csv(conn, _FIXTURE_CSV)
    return conn
