"""Companies stage: dedupe normalized company keys + enrich via Fiber."""
from __future__ import annotations
import sqlite3


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
