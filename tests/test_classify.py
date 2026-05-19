from pathlib import Path
import sqlite3
import pytest

from netcrm import db, ingest, classify
from netcrm._stubs import StubClassifierClient
from netcrm.cost import CostTracker

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"


@pytest.fixture
def populated_db(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    ingest.ingest_csv(conn, FIXTURE_CSV)
    return conn


def test_classify_people_writes_one_row_per_person(populated_db):
    stub = StubClassifierClient()
    tracker = CostTracker(populated_db, max_spend_usd=None)
    classify.classify_people(
        populated_db, stub, tracker,
        model="claude-haiku-4-5-20251001",
        batch_size=5,
        usd_per_input_mtok=1.0, usd_per_output_mtok=5.0,
    )
    n_class = populated_db.execute("SELECT COUNT(*) FROM people_class").fetchone()[0]
    n_people = populated_db.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    assert n_class == n_people


def test_classify_people_marks_founders(populated_db):
    stub = StubClassifierClient()
    tracker = CostTracker(populated_db, max_spend_usd=None)
    classify.classify_people(populated_db, stub, tracker,
                             model="claude-haiku-4-5-20251001",
                             batch_size=5,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    rows = populated_db.execute(
        """
        SELECT p.first_name, pc.role_bucket, pc.seniority
        FROM people p JOIN people_class pc USING(linkedin_url)
        WHERE pc.role_bucket = 'Founder'
        """
    ).fetchall()
    names = {r["first_name"] for r in rows}
    assert {"Carol", "Pete"}.issubset(names)


def test_classify_people_is_idempotent(populated_db):
    stub = StubClassifierClient()
    tracker = CostTracker(populated_db, max_spend_usd=None)
    classify.classify_people(populated_db, stub, tracker,
                             model="claude-haiku-4-5-20251001",
                             batch_size=5,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    first = stub.batches_called
    classify.classify_people(populated_db, stub, tracker,
                             model="claude-haiku-4-5-20251001",
                             batch_size=5,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    assert stub.batches_called == first


def test_classify_people_batches_correctly(populated_db):
    stub = StubClassifierClient()
    tracker = CostTracker(populated_db, max_spend_usd=None)
    classify.classify_people(populated_db, stub, tracker,
                             model="claude-haiku-4-5-20251001",
                             batch_size=5,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    # 20 people / batch_size=5 → 4 batches
    assert stub.batches_called == 4


def test_classify_people_logs_costs(populated_db):
    stub = StubClassifierClient()
    tracker = CostTracker(populated_db, max_spend_usd=None)
    classify.classify_people(populated_db, stub, tracker,
                             model="claude-haiku-4-5-20251001",
                             batch_size=5,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    n_cost = populated_db.execute(
        "SELECT COUNT(*) FROM costs WHERE provider='anthropic'"
    ).fetchone()[0]
    assert n_cost == 4  # one log row per batch
