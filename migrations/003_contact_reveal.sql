-- Person-level reveal data from Fiber Core's syncQuickContactReveal.
-- One row per linkedin_url that's been revealed. Cached forever; force flag
-- on the MCP tool re-runs for a small extra cost.

CREATE TABLE contact_reveal (
  linkedin_url    TEXT PRIMARY KEY,
  work_email      TEXT,
  personal_email  TEXT,
  all_emails      TEXT,                 -- comma-separated; convenient for LIKE queries
  phone_numbers   TEXT,                 -- comma-separated
  status          TEXT NOT NULL,        -- 'ok' | 'not_found' | 'error'
  revealed_at     TIMESTAMP NOT NULL,
  raw_payload     TEXT,                 -- full Fiber response JSON for forensics
  FOREIGN KEY (linkedin_url) REFERENCES people(linkedin_url)
);
CREATE INDEX ix_contact_reveal_status ON contact_reveal(status);

-- Recreate people_enriched to expose the revealed contact fields.
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
  cr.work_email      AS revealed_work_email,
  cr.personal_email  AS revealed_personal_email,
  cr.all_emails      AS revealed_all_emails,
  cr.phone_numbers   AS revealed_phone,
  cr.revealed_at,
  c.fiber_status, pc.classified_at, c.fiber_enriched_at
FROM people p
LEFT JOIN companies      c  ON p.company_key  = c.company_key
LEFT JOIN people_class   pc ON p.linkedin_url = pc.linkedin_url
LEFT JOIN contact_state  cs ON p.linkedin_url = cs.linkedin_url
LEFT JOIN contact_reveal cr ON p.linkedin_url = cr.linkedin_url;
