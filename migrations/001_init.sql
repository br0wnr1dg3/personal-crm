-- People: one row per LinkedIn connection
CREATE TABLE people (
  linkedin_url    TEXT PRIMARY KEY,
  first_name      TEXT,
  last_name       TEXT,
  email           TEXT,
  raw_company     TEXT,
  raw_position    TEXT,
  connected_on    DATE,
  company_key     TEXT,
  imported_at     TIMESTAMP NOT NULL,
  source_csv_sha  TEXT NOT NULL
);
CREATE INDEX ix_people_company_key  ON people(company_key);
CREATE INDEX ix_people_connected_on ON people(connected_on);

-- Companies: one row per normalized company name
CREATE TABLE companies (
  company_key       TEXT PRIMARY KEY,
  display_name      TEXT NOT NULL,
  industry          TEXT,
  sub_industry      TEXT,
  employee_band     TEXT,
  revenue_band      TEXT,
  funding_stage     TEXT,
  hq_country        TEXT,
  hq_region         TEXT,
  website           TEXT,
  description       TEXT,
  fiber_enriched_at TIMESTAMP,
  fiber_status      TEXT
);
CREATE INDEX ix_companies_industry      ON companies(industry);
CREATE INDEX ix_companies_employee_band ON companies(employee_band);
CREATE INDEX ix_companies_funding_stage ON companies(funding_stage);

-- People classification: one row per person, written by classify-people stage
CREATE TABLE people_class (
  linkedin_url     TEXT PRIMARY KEY,
  role_bucket      TEXT NOT NULL,
  seniority        TEXT NOT NULL,
  classified_at    TIMESTAMP NOT NULL,
  classifier_model TEXT NOT NULL,
  FOREIGN KEY (linkedin_url) REFERENCES people(linkedin_url)
);

-- Cost log: one row per Fiber/Haiku call
CREATE TABLE costs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TIMESTAMP NOT NULL,
  provider   TEXT NOT NULL,
  operation  TEXT NOT NULL,
  units      INTEGER NOT NULL,
  usd_cost   REAL    NOT NULL,
  context    TEXT
);
CREATE INDEX ix_costs_provider ON costs(provider);
