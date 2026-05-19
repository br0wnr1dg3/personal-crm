from datetime import date
from pathlib import Path
import sqlite3
import pytest

from netcrm import db, ingest

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "tiny_connections.csv"
REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def initialized_db(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    return conn


def test_parse_row_basic():
    row = {
        "First Name": "Alice",
        "Last Name": "Smith",
        "URL": "https://www.linkedin.com/in/alice-smith-1",
        "Email Address": "alice@example.com",
        "Company": "Acme Inc.",
        "Position": "VP of Sales",
        "Connected On": "15 May 2026",
    }
    parsed = ingest.parse_row(row)
    assert parsed["linkedin_url"] == "https://www.linkedin.com/in/alice-smith-1"
    assert parsed["first_name"] == "Alice"
    assert parsed["connected_on"] == date(2026, 5, 15)
    assert parsed["company_key"] == "acme"
    assert parsed["raw_company"] == "Acme Inc."


def test_parse_row_missing_optional_fields():
    row = {
        "First Name": "Frank",
        "Last Name": "Brown",
        "URL": "https://www.linkedin.com/in/frank-brown-6",
        "Email Address": "",
        "Company": "",
        "Position": "Consultant",
        "Connected On": "05 Aug 2023",
    }
    parsed = ingest.parse_row(row)
    assert parsed["email"] == "" or parsed["email"] is None
    assert parsed["raw_company"] == "" or parsed["raw_company"] is None
    assert parsed["company_key"] == ""


def test_ingest_csv_skips_preamble_and_loads_rows(initialized_db):
    ingest.ingest_csv(initialized_db, FIXTURE_CSV)
    n = initialized_db.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    assert n == 20


def test_ingest_csv_is_idempotent(initialized_db):
    ingest.ingest_csv(initialized_db, FIXTURE_CSV)
    ingest.ingest_csv(initialized_db, FIXTURE_CSV)
    n = initialized_db.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    assert n == 20


def test_ingest_csv_rejects_unexpected_headers(initialized_db, tmp_path: Path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Foo,Bar,Baz\nx,y,z\n")
    with pytest.raises(ValueError, match="unexpected"):
        ingest.ingest_csv(initialized_db, bad)
