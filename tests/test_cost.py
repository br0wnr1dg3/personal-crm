from pathlib import Path
import sqlite3
import pytest

from netcrm import db, cost

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def conn(tmp_db_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_db_path)
    db.apply_migrations(c, REPO_ROOT / "migrations")
    return c


def test_log_call_writes_row(conn):
    tracker = cost.CostTracker(conn, max_spend_usd=None)
    tracker.log(provider="fiber", operation="org_enrich", units=1,
                usd_cost=0.02, context="acme")
    rows = conn.execute("SELECT provider, usd_cost, context FROM costs").fetchall()
    assert len(rows) == 1
    assert rows[0]["provider"] == "fiber"
    assert rows[0]["usd_cost"] == pytest.approx(0.02)
    assert rows[0]["context"] == "acme"


def test_running_total_tracks_spend(conn):
    tracker = cost.CostTracker(conn, max_spend_usd=None)
    tracker.log("fiber", "org_enrich", 1, 0.02, "a")
    tracker.log("fiber", "org_enrich", 1, 0.03, "b")
    assert tracker.spent_usd == pytest.approx(0.05)


def test_max_spend_aborts_when_exceeded(conn):
    tracker = cost.CostTracker(conn, max_spend_usd=0.05)
    tracker.log("fiber", "org_enrich", 1, 0.04, "a")
    with pytest.raises(cost.SpendCapExceeded):
        tracker.log("fiber", "org_enrich", 1, 0.02, "b")


def test_estimate_fiber_calls():
    # 100 unenriched companies at $0.02/credit, 1 credit each
    est = cost.estimate_fiber(unenriched_count=100, usd_per_credit=0.02,
                              credits_per_call=1)
    assert est == pytest.approx(2.00)


def test_estimate_haiku_calls():
    # 4800 people in batches of 50 = 96 calls.
    # Per-call ~600 input toks + 200 output toks.
    # Use a passed-in price model so we don't hardcode rates here.
    est = cost.estimate_haiku(
        unclassified_count=4800, batch_size=50,
        input_tokens_per_call=600, output_tokens_per_call=200,
        usd_per_input_mtok=1.00, usd_per_output_mtok=5.00,
    )
    # 96 calls * (600*1e-6*1.0 + 200*1e-6*5.0) = 96 * (0.0006 + 0.001) = 96 * 0.0016 = 0.1536
    assert est == pytest.approx(0.1536)
