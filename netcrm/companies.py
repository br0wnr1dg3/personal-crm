"""Companies stage: dedupe normalized company keys + enrich via Fiber."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from typing import Protocol

from netcrm.cost import CostTracker
from netcrm.fiber import FiberEnrichment, FiberStatus


def dedupe_companies(conn: sqlite3.Connection) -> int:
    """Populate the companies table from distinct people.company_key.

    For each key, picks the first-seen raw_company (lowest rowid) as display_name.
    Existing companies rows are left untouched (we never overwrite display_name
    or enrichment fields here).
    """
    rows = conn.execute(
        """
        SELECT p.company_key, p.raw_company
        FROM people p
        WHERE p.company_key != ''
          AND p.rowid = (
            SELECT MIN(p2.rowid) FROM people p2
            WHERE p2.company_key = p.company_key AND p2.raw_company != ''
          )
        """
    ).fetchall()
    with conn:
        conn.executemany(
            """
            INSERT INTO companies(company_key, display_name)
            VALUES (?, ?)
            ON CONFLICT(company_key) DO NOTHING
            """,
            [(r["company_key"], r["raw_company"]) for r in rows],
        )
    return conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]


class _FiberLike(Protocol):
    def enrich(self, name: str) -> FiberEnrichment: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def count_unenriched(conn: sqlite3.Connection) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM companies
        WHERE fiber_enriched_at IS NULL
          AND (fiber_status IS NULL OR fiber_status = 'error')
        """
    ).fetchone()[0]


def enrich_companies(
    conn: sqlite3.Connection,
    fiber: _FiberLike,
    cost: CostTracker,
    usd_per_credit: float,
) -> int:
    """Enrich every un-enriched company. Returns number of API calls made."""
    rows = conn.execute(
        """
        SELECT company_key, display_name FROM companies
        WHERE fiber_enriched_at IS NULL
          AND (fiber_status IS NULL OR fiber_status = 'error')
        ORDER BY company_key
        """
    ).fetchall()
    n_calls = 0
    for row in rows:
        result: FiberEnrichment = fiber.enrich(row["display_name"])
        n_calls += 1
        cost.log(
            provider="fiber", operation="org_enrich",
            units=result.units, usd_cost=result.units * usd_per_credit,
            context=row["company_key"],
        )
        with conn:
            conn.execute(
                """
                UPDATE companies SET
                  industry=?, sub_industry=?, employee_band=?, revenue_band=?,
                  funding_stage=?, hq_country=?, hq_region=?, website=?,
                  description=?,
                  fiber_enriched_at=?, fiber_status=?
                WHERE company_key=?
                """,
                (
                    result.industry, result.sub_industry, result.employee_band,
                    result.revenue_band, result.funding_stage,
                    result.hq_country, result.hq_region, result.website,
                    result.description,
                    # error: leave fiber_enriched_at NULL so we retry next run
                    None if result.status == FiberStatus.ERROR else _now_iso(),
                    result.status.value,
                    row["company_key"],
                ),
            )
    return n_calls
