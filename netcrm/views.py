"""Create the people_enriched view and any future read-only views."""
from __future__ import annotations
import sqlite3

PEOPLE_ENRICHED_SQL = """
CREATE VIEW IF NOT EXISTS people_enriched AS
SELECT
  p.linkedin_url, p.first_name, p.last_name, p.email,
  p.raw_position, p.connected_on,
  c.company_key, c.display_name AS company_name,
  c.industry, c.sub_industry, c.employee_band, c.revenue_band,
  c.funding_stage, c.hq_country, c.hq_region, c.website,
  pc.role_bucket, pc.seniority,
  c.fiber_status, pc.classified_at, c.fiber_enriched_at
FROM people p
LEFT JOIN companies    c  ON p.company_key  = c.company_key
LEFT JOIN people_class pc ON p.linkedin_url = pc.linkedin_url;
"""


def build_views(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executescript(PEOPLE_ENRICHED_SQL)
