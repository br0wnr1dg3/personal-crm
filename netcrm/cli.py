"""Typer entrypoint. Each sub-command maps 1:1 to a pipeline stage."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv

from netcrm import db, ingest, companies, classify, views
from netcrm.cost import CostTracker, SpendCapExceeded, estimate_fiber, estimate_haiku

app = typer.Typer(no_args_is_help=True, add_completion=False)

REPO_ROOT = Path(__file__).parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


# ---------- helpers ----------

def _env(name: str, default: Optional[str] = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise typer.BadParameter(f"missing required env var: {name}")
    return val


def _db_path() -> Path:
    return Path(os.environ.get("NETCRM_DB_PATH", "crm.db"))


def _open_db():
    conn = db.connect(_db_path())
    db.apply_migrations(conn, MIGRATIONS_DIR)
    return conn


def _make_fiber_client():
    """In test mode, return a stub keyed on canned JSON. Otherwise real client."""
    if os.environ.get("NETCRM_TEST_MODE"):
        from netcrm._stubs import load_canned_fiber_stub
        return load_canned_fiber_stub(REPO_ROOT / "tests" / "fixtures")
    from netcrm.fiber import FiberClient
    return FiberClient(api_key=_env("FIBER_API_KEY"),
                       base_url=_env("FIBER_API_BASE_URL"))


def _make_classifier_client():
    if os.environ.get("NETCRM_TEST_MODE"):
        from netcrm._stubs import StubClassifierClient
        return StubClassifierClient()
    import anthropic
    from netcrm.anthropic_client import ClassifierClient
    sdk = anthropic.Anthropic(api_key=_env("ANTHROPIC_API_KEY"))
    return ClassifierClient(sdk, model=_env("ANTHROPIC_MODEL",
                                            "claude-haiku-4-5-20251001"))


# ---------- commands ----------

@app.command(name="ingest")
def ingest_cmd(
    csv_path: Path = typer.Argument(..., exists=True, dir_okay=False,
                                    help="Path to LinkedIn connections.csv"),
):
    """Ingest a LinkedIn connections.csv into the people table."""
    load_dotenv()
    conn = _open_db()
    n = ingest.ingest_csv(conn, csv_path)
    typer.echo(f"ingested {n} people rows")


@app.command(name="dedupe-companies")
def dedupe_companies_cmd():
    """Populate companies table from distinct people.company_key."""
    load_dotenv()
    conn = _open_db()
    n = companies.dedupe_companies(conn)
    typer.echo(f"companies table now has {n} rows")


@app.command(name="enrich-companies")
def enrich_companies_cmd(
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_spend_usd: Optional[float] = typer.Option(None, "--max-spend-usd"),
):
    """Enrich every un-enriched company via Fiber AI."""
    load_dotenv()
    conn = _open_db()
    usd_per_credit = float(os.environ.get("FIBER_USD_PER_CREDIT", "0.02"))
    unenriched = companies.count_unenriched(conn)
    est = estimate_fiber(unenriched, usd_per_credit=usd_per_credit)
    if dry_run:
        typer.echo(f"would enrich {unenriched} companies, estimated cost ${est:.2f}")
        return
    if max_spend_usd is not None and est > max_spend_usd:
        typer.echo(f"estimated ${est:.2f} exceeds --max-spend-usd ${max_spend_usd:.2f}; aborting",
                   err=True)
        raise typer.Exit(code=2)
    fiber = _make_fiber_client()
    tracker = CostTracker(conn, max_spend_usd=max_spend_usd)
    try:
        n = companies.enrich_companies(conn, fiber, tracker, usd_per_credit=usd_per_credit)
    except SpendCapExceeded as e:
        typer.echo(f"aborted: {e}", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"enriched {n} companies, cost ${tracker.spent_usd:.2f}")


@app.command(name="classify-people")
def classify_people_cmd(
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_spend_usd: Optional[float] = typer.Option(None, "--max-spend-usd"),
    batch_size: int = typer.Option(50, "--batch-size"),
):
    """Classify every un-classified person via Haiku."""
    load_dotenv()
    conn = _open_db()
    unclassified = classify.count_unclassified(conn)
    usd_in = float(os.environ.get("HAIKU_USD_PER_INPUT_MTOK", "1.00"))
    usd_out = float(os.environ.get("HAIKU_USD_PER_OUTPUT_MTOK", "5.00"))
    est = estimate_haiku(unclassified, batch_size=batch_size,
                         input_tokens_per_call=600, output_tokens_per_call=200,
                         usd_per_input_mtok=usd_in,
                         usd_per_output_mtok=usd_out)
    if dry_run:
        typer.echo(f"would classify {unclassified} people in batches of "
                   f"{batch_size}, estimated cost ${est:.4f}")
        return
    if max_spend_usd is not None and est > max_spend_usd:
        typer.echo(f"estimated ${est:.4f} exceeds --max-spend-usd ${max_spend_usd:.2f}",
                   err=True)
        raise typer.Exit(code=2)
    classifier = _make_classifier_client()
    tracker = CostTracker(conn, max_spend_usd=max_spend_usd)
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    try:
        n = classify.classify_people(conn, classifier, tracker,
                                     model=model, batch_size=batch_size,
                                     usd_per_input_mtok=usd_in,
                                     usd_per_output_mtok=usd_out)
    except SpendCapExceeded as e:
        typer.echo(f"aborted: {e}", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"classified in {n} batches, cost ${tracker.spent_usd:.4f}")


@app.command(name="build-views")
def build_views_cmd():
    """(Re-)create people_enriched view."""
    load_dotenv()
    conn = _open_db()
    views.build_views(conn)
    typer.echo("views built")


@app.command(name="run-all")
def run_all_cmd(
    csv_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    max_spend_usd: Optional[float] = typer.Option(None, "--max-spend-usd"),
):
    """Run all stages end-to-end."""
    load_dotenv()
    conn = _open_db()
    ingest.ingest_csv(conn, csv_path)
    companies.dedupe_companies(conn)
    enrich_companies_cmd(dry_run=False, max_spend_usd=max_spend_usd)
    classify_people_cmd(dry_run=False, max_spend_usd=max_spend_usd, batch_size=50)
    views.build_views(conn)
    typer.echo("run-all complete")


@app.command(name="cost-report")
def cost_report_cmd():
    """Print cumulative spend per provider."""
    load_dotenv()
    conn = _open_db()
    rows = conn.execute(
        "SELECT provider, SUM(usd_cost) AS usd FROM costs GROUP BY provider"
    ).fetchall()
    if not rows:
        typer.echo("no costs logged")
        return
    for r in rows:
        typer.echo(f"{r['provider']:12s} ${r['usd']:.4f}")


@app.command(name="serve")
def serve_cmd():
    """Run datasette against crm.db with metadata.yml."""
    import subprocess
    subprocess.run(
        ["datasette", "serve", str(_db_path()),
         "--metadata", str(REPO_ROOT / "metadata.yml")],
        check=False,
    )
