-- Per-contact agent state: tags, freeform notes, last outreach timestamp.
-- Populated by MCP tools (mcp_server.py); queried via the people_enriched view.

CREATE TABLE contact_state (
  linkedin_url     TEXT PRIMARY KEY,
  tags             TEXT,           -- comma-separated, lowercased
  notes            TEXT,
  last_outreach_at TIMESTAMP,
  updated_at       TIMESTAMP NOT NULL,
  FOREIGN KEY (linkedin_url) REFERENCES people(linkedin_url)
);

CREATE INDEX ix_contact_state_last_outreach ON contact_state(last_outreach_at);

-- Replace people_enriched view to include contact_state columns.
DROP VIEW IF EXISTS people_enriched;
CREATE VIEW people_enriched AS
SELECT
  p.linkedin_url, p.first_name, p.last_name, p.email,
  p.raw_position, p.connected_on,
  c.company_key, c.display_name AS company_name,
  c.industry, c.sub_industry, c.employee_band, c.revenue_band,
  c.funding_stage, c.hq_country, c.hq_region, c.website,
  pc.role_bucket, pc.seniority,
  cs.tags, cs.notes, cs.last_outreach_at,
  c.fiber_status, pc.classified_at, c.fiber_enriched_at
FROM people p
LEFT JOIN companies     c  ON p.company_key  = c.company_key
LEFT JOIN people_class  pc ON p.linkedin_url = pc.linkedin_url
LEFT JOIN contact_state cs ON p.linkedin_url = cs.linkedin_url;
