from pathlib import Path
import sqlite3
import pytest

from netcrm import db, ingest, companies

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"


@pytest.fixture
def populated_db(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    ingest.ingest_csv(conn, FIXTURE_CSV)
    return conn


def test_dedupe_companies_creates_one_row_per_key(populated_db):
    n = companies.dedupe_companies(populated_db)
    rows = populated_db.execute(
        "SELECT company_key, display_name FROM companies ORDER BY company_key"
    ).fetchall()
    keys = [r["company_key"] for r in rows]
    # 'acme' merges 3 variants; 'stark industries' merges 2; 'globex' merges 2; 'nuts and bolts ai' merges 2
    # Plus: initech, umbrella corp, soylent, tesla, massive dynamic, wayne enterprises, sequoia capital, cogent labs
    # Plus blank "" key for the 2 rows with no company
    assert "acme" in keys
    assert "stark industries" in keys
    assert "globex" in keys
    assert "nuts and bolts ai" in keys
    assert n == len(keys)
    # display_name preserves a real raw value (first-seen)
    acme_row = next(r for r in rows if r["company_key"] == "acme")
    assert acme_row["display_name"] in {"Acme Inc.", "ACME, Inc", "Acme Inc"}


def test_dedupe_companies_is_idempotent(populated_db):
    companies.dedupe_companies(populated_db)
    n_before = populated_db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    companies.dedupe_companies(populated_db)
    n_after = populated_db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    assert n_before == n_after


def test_dedupe_companies_skips_blank_key(populated_db):
    """Two rows have no Company; they should not produce a '' companies row."""
    companies.dedupe_companies(populated_db)
    blank = populated_db.execute(
        "SELECT COUNT(*) FROM companies WHERE company_key = ''"
    ).fetchone()[0]
    assert blank == 0
