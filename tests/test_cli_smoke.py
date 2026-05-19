"""End-to-end smoke test: stub Fiber + stub Anthropic, full pipeline via CLI."""
from pathlib import Path
import json
import os
import sqlite3
import pytest
from typer.testing import CliRunner

from netcrm.cli import app

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"


@pytest.fixture
def smoke_env(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "smoke.db"
    monkeypatch.setenv("NETCRM_DB_PATH", str(db_path))
    monkeypatch.setenv("FIBER_API_KEY", "test")
    monkeypatch.setenv("FIBER_API_BASE_URL", "https://api.fiber.test")
    monkeypatch.setenv("FIBER_USD_PER_CREDIT", "0.02")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("NETCRM_TEST_MODE", "1")  # signal: use stubs
    return db_path


def test_cli_run_all_smoke(smoke_env):
    runner = CliRunner()
    result = runner.invoke(app, ["run-all", str(FIXTURE_CSV)])
    assert result.exit_code == 0, result.stdout
    conn = sqlite3.connect(smoke_env)
    n_people = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    n_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    n_class = conn.execute("SELECT COUNT(*) FROM people_class").fetchone()[0]
    assert n_people == 20
    assert n_companies > 0
    assert n_class == 20
    # view exists
    n_view = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name='people_enriched'"
    ).fetchone()[0]
    assert n_view == 1
    conn.close()


def test_cli_dry_run_enrich(smoke_env):
    runner = CliRunner()
    runner.invoke(app, ["ingest", str(FIXTURE_CSV)])
    runner.invoke(app, ["dedupe-companies"])
    result = runner.invoke(app, ["enrich-companies", "--dry-run"])
    assert result.exit_code == 0
    assert "would enrich" in result.stdout.lower()


def test_cli_idempotent(smoke_env):
    runner = CliRunner()
    runner.invoke(app, ["run-all", str(FIXTURE_CSV)])
    conn = sqlite3.connect(smoke_env)
    n_costs_first = conn.execute("SELECT COUNT(*) FROM costs").fetchone()[0]
    conn.close()
    runner.invoke(app, ["run-all", str(FIXTURE_CSV)])
    conn = sqlite3.connect(smoke_env)
    n_costs_second = conn.execute("SELECT COUNT(*) FROM costs").fetchone()[0]
    conn.close()
    assert n_costs_second == n_costs_first  # no new API calls on re-run
