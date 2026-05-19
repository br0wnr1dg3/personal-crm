from pathlib import Path
import sqlite3
import pytest
import yaml

from netcrm import db, ingest, companies, classify, views
from netcrm.cost import CostTracker
from netcrm._stubs import StubClassifierClient, load_canned_fiber_stub

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
SAVED = REPO_ROOT / "saved_queries"


@pytest.fixture
def loaded(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    ingest.ingest_csv(conn, FIXTURE_CSV)
    companies.dedupe_companies(conn)
    companies.enrich_companies(conn, load_canned_fiber_stub(FIXTURES_DIR),
                               CostTracker(conn, None), 0.02)
    classify.classify_people(conn, StubClassifierClient(),
                             CostTracker(conn, None),
                             model="claude-haiku-4-5-20251001",
                             batch_size=10,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    views.build_views(conn)
    return conn


@pytest.mark.parametrize("query_file", [
    "reconnect_targets.sql",
    "sales_at_growth_companies.sql",
    "marketing_leaders.sql",
    "founders_in_target_industries.sql",
])
def test_saved_query_parses(loaded, query_file):
    sql = (SAVED / query_file).read_text()
    # must not error; result may be empty on the tiny fixture, that's fine
    rows = loaded.execute(sql).fetchall()
    assert isinstance(rows, list)


def test_metadata_yml_references_saved_queries():
    meta = yaml.safe_load((REPO_ROOT / "metadata.yml").read_text())
    db_meta = meta["databases"]["crm"]
    query_names = set(db_meta["queries"].keys())
    expected = {
        "reconnect_targets", "sales_at_growth_companies",
        "marketing_leaders", "founders_in_target_industries",
    }
    assert expected.issubset(query_names)
