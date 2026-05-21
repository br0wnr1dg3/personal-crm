from pathlib import Path
import sqlite3
import pytest

from netcrm import db, ingest, companies, classify, views
from netcrm.cost import CostTracker
from netcrm._stubs import StubFiberClient, StubClassifierClient, load_canned_fiber_stub

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def fully_loaded_db(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    ingest.ingest_csv(conn, FIXTURE_CSV)
    companies.dedupe_companies(conn)
    companies.enrich_companies(conn, load_canned_fiber_stub(FIXTURES_DIR),
                               CostTracker(conn, None), usd_per_credit=0.02)
    classify.classify_people(conn, StubClassifierClient(),
                             CostTracker(conn, None),
                             model="claude-haiku-4-5-20251001",
                             batch_size=10,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    return conn


def test_build_views_creates_people_enriched(fully_loaded_db):
    views.build_views(fully_loaded_db)
    view_names = {
        r[0] for r in fully_loaded_db.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()
    }
    assert "people_enriched" in view_names


def test_people_enriched_joins_company_and_class(fully_loaded_db):
    views.build_views(fully_loaded_db)
    row = fully_loaded_db.execute(
        """
        SELECT first_name, company_name, industry, role_bucket, seniority
        FROM people_enriched
        WHERE first_name = 'Alice'
        """
    ).fetchone()
    assert row["industry"] == "Manufacturing"
    assert row["role_bucket"] == "Sales"
    assert row["seniority"] == "VP"


def test_build_views_is_idempotent(fully_loaded_db):
    views.build_views(fully_loaded_db)
    views.build_views(fully_loaded_db)  # must not raise
    view_names = {
        r[0] for r in fully_loaded_db.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()
    }
    assert "people_enriched" in view_names
