from pathlib import Path
import sqlite3
import json
import pytest

from netcrm import db, ingest, companies
from netcrm.cost import CostTracker
from netcrm._stubs import StubFiberClient

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


@pytest.fixture
def stub_fiber() -> StubFiberClient:
    canned_path = REPO_ROOT / "tests" / "fixtures" / "fiber_canned_responses.json"
    return StubFiberClient(json.loads(canned_path.read_text()))


def test_enrich_companies_writes_ok_rows(populated_db, stub_fiber):
    companies.dedupe_companies(populated_db)
    tracker = CostTracker(populated_db, max_spend_usd=None)
    companies.enrich_companies(populated_db, stub_fiber, tracker,
                               usd_per_credit=0.02)
    row = populated_db.execute(
        "SELECT industry, employee_band, fiber_status FROM companies "
        "WHERE company_key = 'acme'"
    ).fetchone()
    assert row["fiber_status"] == "ok"
    assert row["industry"] == "Manufacturing"


def test_enrich_companies_writes_not_found(populated_db, stub_fiber):
    companies.dedupe_companies(populated_db)
    tracker = CostTracker(populated_db, max_spend_usd=None)
    companies.enrich_companies(populated_db, stub_fiber, tracker,
                               usd_per_credit=0.02)
    # 'umbrella' is not in canned responses ('Umbrella Corp' strips to 'umbrella')
    row = populated_db.execute(
        "SELECT industry, fiber_status FROM companies "
        "WHERE company_key = 'umbrella'"
    ).fetchone()
    assert row["fiber_status"] == "not_found"
    assert row["industry"] is None


def test_enrich_companies_skips_already_enriched(populated_db, stub_fiber):
    companies.dedupe_companies(populated_db)
    tracker = CostTracker(populated_db, max_spend_usd=None)
    companies.enrich_companies(populated_db, stub_fiber, tracker, usd_per_credit=0.02)
    n_calls_first = len(stub_fiber.calls)
    companies.enrich_companies(populated_db, stub_fiber, tracker, usd_per_credit=0.02)
    n_calls_second = len(stub_fiber.calls)
    assert n_calls_second == n_calls_first  # nothing re-enriched


def test_enrich_companies_logs_costs(populated_db, stub_fiber):
    companies.dedupe_companies(populated_db)
    tracker = CostTracker(populated_db, max_spend_usd=None)
    companies.enrich_companies(populated_db, stub_fiber, tracker, usd_per_credit=0.02)
    n_cost_rows = populated_db.execute(
        "SELECT COUNT(*) FROM costs WHERE provider='fiber'"
    ).fetchone()[0]
    n_companies = populated_db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    # Every enrichment attempt logs one cost row (ok + not_found both cost a credit)
    assert n_cost_rows == n_companies


def test_enrich_companies_retries_error_status(populated_db, stub_fiber):
    """ERROR-status rows are picked back up; NOT_FOUND/PERMANENT_ERROR are not."""
    companies.dedupe_companies(populated_db)
    # Seed 'acme' with fiber_status='error' and fiber_enriched_at NULL (the error state)
    with populated_db:
        populated_db.execute(
            "UPDATE companies SET fiber_status = 'error', fiber_enriched_at = NULL "
            "WHERE company_key = 'acme'"
        )
    stub_fiber.calls.clear()  # ignore any prior call tracking
    tracker = CostTracker(populated_db, max_spend_usd=None)
    companies.enrich_companies(populated_db, stub_fiber, tracker, usd_per_credit=0.02)
    # Acme should have been re-queried (display_name "Acme Inc." or similar)
    assert any("Acme" in name for name in stub_fiber.calls), (
        f"expected 'Acme' to be in re-queried names; got {stub_fiber.calls!r}"
    )
    # And it succeeded this time
    row = populated_db.execute(
        "SELECT fiber_status, industry FROM companies WHERE company_key = 'acme'"
    ).fetchone()
    assert row["fiber_status"] == "ok"
    assert row["industry"] == "Manufacturing"
