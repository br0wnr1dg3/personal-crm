# Personal Network CRM — Design

**Date:** 2026-05-19
**Status:** Draft — pending user approval before implementation plan
**Owner:** chris@nutsandbolts.ai

## Summary

A local Python pipeline that turns a ~4,800-row LinkedIn `connections.csv` export into an enriched, queryable SQLite database, served via Datasette. The dataset is built once per CSV export (incremental on re-runs), with company-level intelligence from Fiber AI and role/seniority classification from Anthropic Haiku. No live dashboards, no CRM workflow features, no LinkedIn post activity in v1 — just a personal "Rolodex 2.0" you can slice with SQL.

The goal is one queryable dataset reusable across the owner's businesses. Each business's targeting becomes a saved SQL query, not a baked-in ICP definition.

## Goals

1. Take the LinkedIn export as-is and produce a SQLite DB where each contact has a normalized company linked to enriched fields (industry, size, funding, region) and a classified role bucket + seniority.
2. Make the enrichment **idempotent and incremental**: re-running on a fresh CSV only enriches new companies and classifies new people.
3. Make the dataset **queryable without code**: Datasette UI for browsing, saved `.sql` files in-repo for repeatable slices.
4. Keep cost bounded and visible: dry-run estimates, spend caps, and a per-call cost log.

## Non-goals (v1)

- LinkedIn post activity. Out of scope; LinkedIn's export omits it, and third-party APIs are out of v1.
- Ongoing CRM workflow (outreach logging, reminders, conversation tracking).
- "Who to reconnect with" as a numeric score column. Instead, it's a saved SQL query that the user tunes.
- Per-business ICP definitions. The schema stays neutral; ICP lives in saved queries.
- Multi-user access, auth, hosting. Single-user local tool.
- Real-time enrichment refresh. Refresh is manual on re-run.

## Out-of-scope but designed to leave room for

- Adding a `posts` table later, fed by Proxycurl or similar (the schema already isolates posts cleanly via the `linkedin_url` join key).
- Adding a `notes`/`interactions` table later if v1 graduates into a working CRM. The current schema doesn't preclude it.

## Architecture

A small Python project run from the CLI. The pipeline is staged; each stage is idempotent and writes its results to its own table or columns. Stages can run independently:

```
connections.csv
      │
      ▼
[ ingest ]              → people (verbatim CSV + normalized fields)
      │
      ▼
[ dedupe-companies ]    → companies (one row per normalized company name)
      │
      ▼
[ enrich-companies ]    → companies (Fiber AI fills industry, size, funding, region…)
      │
      ▼
[ classify-people ]     → people_class (Haiku fills role_bucket, seniority)
      │
      ▼
[ build-views ]         → SQL views joining the above
      │
      ▼
$ datasette serve crm.db          (web UI + raw SQL + saved queries)
```

**Why staged + cached, not one big script.** Fiber calls cost real money; if `classify-people` crashes at row 4,200 we don't want to re-pay Fiber on retry. When LinkedIn ships a fresh CSV in 3 months, only new connections need enrichment.

**Why SQLite + Datasette.** ~4,800 rows is trivial for SQLite. Datasette provides a free grid-and-SQL web UI, exposes saved queries (via `metadata.yml` or the `datasette-query-files` plugin), and exports results as CSV/JSON. No server to babysit.

**Why a separate repo from cma-starter.** Personal tooling; different lifecycle; reusing cma-starter's repo would muddy that project's purpose. Direct Fiber HTTP API calls keep this project free of cma-starter coupling.

## Project layout

```
~/Developer/projects/personal-network-crm/
  netcrm/
    __init__.py
    cli.py              # typer/argparse entrypoint; sub-commands map 1:1 to stages
    ingest.py           # CSV → people table
    companies.py        # company-name normalization + dedupe + Fiber enrichment
    classify.py         # Haiku role + seniority classification
    views.py            # CREATE VIEW SQL
    db.py               # connection, migrations, schema helpers
    fiber.py            # thin HTTP client over Fiber AI
    anthropic_client.py # thin batched Haiku client
    cost.py             # dry-run estimator + spend cap + cost log writer
  migrations/
    001_init.sql
    002_*.sql           # future additive migrations
  saved_queries/
    reconnect_targets.sql
    sales_at_growth_companies.sql
    marketing_leaders.sql
    founders_in_target_industries.sql
  tests/
    fixtures/
      tiny_connections.csv      # ~20 hand-picked edge-case rows
      fiber_canned_responses.json
    test_normalize.py
    test_ingest.py
    test_classify_prompt.py
    test_idempotency.py
    test_cost.py
  metadata.yml          # Datasette config: db title, canned queries pointing at saved_queries/*.sql
  pyproject.toml
  README.md
  .env.example          # FIBER_API_KEY=, ANTHROPIC_API_KEY=, FIBER_USD_PER_CREDIT=
  .gitignore            # crm.db, .env, .venv, __pycache__/
```

## Data model

Three core tables plus one view. SQL is the source of truth; lives in `migrations/001_init.sql`.

```sql
-- Verbatim CSV rows, plus the normalized company key
CREATE TABLE people (
  linkedin_url    TEXT PRIMARY KEY,         -- stable id across re-exports
  first_name      TEXT,
  last_name       TEXT,
  email           TEXT,                      -- often empty in LinkedIn exports
  raw_company     TEXT,                      -- as-typed
  raw_position    TEXT,
  connected_on    DATE,                      -- parsed from "15 May 2026"
  company_key     TEXT,                      -- FK → companies.company_key
  imported_at     TIMESTAMP NOT NULL,
  source_csv_sha  TEXT NOT NULL,             -- which CSV import this row came from
  FOREIGN KEY (company_key) REFERENCES companies(company_key)
);
CREATE INDEX ix_people_company_key  ON people(company_key);
CREATE INDEX ix_people_connected_on ON people(connected_on);

-- One row per normalized company name
CREATE TABLE companies (
  company_key       TEXT PRIMARY KEY,        -- lowercased, punctuation stripped, "inc/ltd" suffixes removed
  display_name      TEXT NOT NULL,           -- first-seen raw_company

  -- Fiber enrichment (NULL until enriched)
  industry          TEXT,
  sub_industry      TEXT,
  employee_band     TEXT,                    -- "1-10","11-50","51-200","201-500","501-1000","1001-5000","5001-10000","10001+"
  revenue_band      TEXT,                    -- "<$1M","$1M-$10M","$10M-$50M","$50M-$100M","$100M-$500M","$500M-$1B","$1B+"
  funding_stage     TEXT,                    -- "Bootstrapped","Seed","Series A","Series B","Series C+","Public","Acquired","Unknown"
  hq_country        TEXT,
  hq_region         TEXT,
  website           TEXT,
  description       TEXT,

  fiber_enriched_at TIMESTAMP,               -- NULL ⇒ not yet enriched
  fiber_status      TEXT                     -- 'ok' | 'not_found' | 'error' | 'permanent_error'
);
CREATE INDEX ix_companies_industry     ON companies(industry);
CREATE INDEX ix_companies_employee_band ON companies(employee_band);
CREATE INDEX ix_companies_funding_stage ON companies(funding_stage);

-- One row per person; written by classify-people stage
CREATE TABLE people_class (
  linkedin_url     TEXT PRIMARY KEY,
  role_bucket      TEXT NOT NULL,            -- enum below; defaults to 'Other'
  seniority        TEXT NOT NULL,            -- enum below; defaults to 'Unknown'
  classified_at    TIMESTAMP NOT NULL,
  classifier_model TEXT NOT NULL,            -- e.g. 'claude-haiku-4-5-20251001'
  FOREIGN KEY (linkedin_url) REFERENCES people(linkedin_url)
);

-- Convenience view; what users actually query
CREATE VIEW people_enriched AS
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

-- Cost log; written by every Fiber/Haiku call
CREATE TABLE costs (
  id         INTEGER PRIMARY KEY,
  ts         TIMESTAMP NOT NULL,
  provider   TEXT NOT NULL,                  -- 'fiber' | 'anthropic'
  operation  TEXT NOT NULL,                  -- 'org_enrich' | 'classify_batch'
  units      INTEGER NOT NULL,               -- credits or tokens
  usd_cost   REAL    NOT NULL,
  context    TEXT                            -- e.g. company_key, batch_size
);

-- Migration bookkeeping
CREATE TABLE _migrations (
  filename   TEXT PRIMARY KEY,
  applied_at TIMESTAMP NOT NULL
);
```

**Why normalize companies.** ~4,800 people likely span 1,500–2,500 unique companies. One Fiber call per company instead of per person — order-of-magnitude cheaper.

**Why `linkedin_url` as PK.** Stable across CSV re-exports; LinkedIn does not change profile URLs. Avoids name-collision issues.

**Company-name normalization rules** (in `companies.py`):
- Lowercase.
- Strip leading/trailing whitespace.
- Remove trailing legal suffixes: `, inc.`, `, ltd.`, `, llc`, `, gmbh`, `, plc`, `, sa`, `, srl`, `, b.v.`, etc. (configurable list).
- Collapse interior whitespace runs.
- Strip punctuation (`.`, `,`, `&` → `and`).
- The result is `company_key`. Original-cased first-seen value is preserved as `display_name`.

Two contacts who typed "Acme Inc." and "ACME, Inc" map to the same `company_key=acme`. Edge cases (homonyms across industries) accepted; not solving for them in v1.

## Classification taxonomy

`classify-people` calls Anthropic Haiku with batches of `(raw_position, raw_company) → {role_bucket, seniority}` and writes one row per person to `people_class`. Output is structured via tool-use JSON schema; no string parsing.

**role_bucket** (single value; defaults to `Other`):

| Bucket | Includes (examples) |
|---|---|
| `Sales` | AE, SDR, BDR, Sales Director, CRO, Account Executive, Sales Engineer |
| `BD` | Business Development, Partnerships, Strategic Alliances |
| `Marketing` | Marketing, Growth, Brand, Content, Demand Gen, Field Marketing |
| `Engineering` | SWE, EM, Architect, DevOps, SRE, Platform |
| `Product` | PM, Product Director, CPO |
| `Design` | Designer, UX, Brand Designer, Design Director |
| `Founder` | Founder, Co-founder, Owner. Overrides title-based seniority → `Founder` |
| `Investor` | VC Partner, Principal, Angel, PE |
| `Operations` | Ops, COO, Finance, HR, Legal, Recruiting |
| `Consulting` | Consultant, Advisor (unless clearly Founder) |
| `Student` | Student, Intern, Graduate |
| `Other` | Fallback |

**seniority** (single value; defaults to `Unknown`):

`IC` · `Manager` · `Director` · `VP` · `C-suite` · `Founder` · `Unknown`

**Prompt-level rules** (encoded as in-prompt examples, not separate logic):
- "Head of X" → `Director` if company is large, `VP` otherwise (Haiku decides from company size hint in the input).
- "Founding [whatever]" → keep role_bucket but seniority=`Founder`.
- Multi-titled people ("Founder & CEO at X, Advisor at Y") → use the *first* title block from `raw_position`.

**Batching.** 50 contacts per Haiku call (~few hundred tokens out per call). For 4,800 people that's ~96 calls. At Haiku 4.5 rates this is <$2 total.

## Fiber enrichment

Direct HTTPS calls to Fiber AI's organization-enrichment endpoint, authenticated with `FIBER_API_KEY` from `.env`. One call per unique normalized company.

**Fields persisted from Fiber response:**
`industry, sub_industry, employee_band, revenue_band, funding_stage, hq_country, hq_region, website, description`

**Per-row outcomes:**
- Fiber returns a match → `fiber_status='ok'`, fields populated, `fiber_enriched_at=now()`.
- Fiber returns no match → `fiber_status='not_found'`, fields null, `fiber_enriched_at=now()`. Won't retry.
- HTTP error / rate limit → `fiber_status='error'`, `fiber_enriched_at` left NULL. Will retry on next run.
- Persistent 4xx (e.g. malformed company name) → `fiber_status='permanent_error'`. Won't retry.

**Cache check before every call.** `SELECT … FROM companies WHERE fiber_enriched_at IS NULL AND (fiber_status IS NULL OR fiber_status='error')` — only those get hit. Idempotent.

**Concurrency.** Async HTTP with a semaphore of 5 in-flight calls. Polite to Fiber and keeps total wall time reasonable.

## Cost guardrails

The pipeline never bills the user without an explicit on-ramp.

1. **Dry-run mode.** `netcrm enrich-companies --dry-run` and `netcrm classify-people --dry-run` print:
   - Count of items that *would* be processed (cache-aware).
   - Per-item cost estimate × count = total estimate, in USD.
   - Then exits with no API calls made.
2. **Spend cap.** `--max-spend-usd N` (default unset = no cap). At enrichment start, estimated cost is computed; if it exceeds the cap, abort with a clear error. During run, cumulative spend is tracked in-memory; the run aborts cleanly if the cap is crossed mid-run.
3. **Cost log.** Every Fiber and Anthropic call writes a row to `costs` (provider, operation, units, usd_cost, context). After-the-fact analysis: `SELECT provider, SUM(usd_cost) FROM costs GROUP BY provider`.
4. **Per-stage one-line summary.** Each command prints, on completion, `enriched 1,847 companies (skipped 384 cached), cost $12.34, took 4m 12s`.

Fiber per-credit USD rate is configurable via `FIBER_USD_PER_CREDIT` env var (defaults to Prospector tier $0.020). Anthropic Haiku costs computed from API response `usage` fields.

## Error handling

Boring and verbose. Errors should never silently corrupt state.

- **Fiber/Anthropic transient errors:** caught at the client layer, logged, status field written, run continues. Re-runnable.
- **Anthropic schema-validation failure** (Haiku returns malformed JSON): retry once with a stricter prompt; on second failure, write `role_bucket='Other', seniority='Unknown'` and a `classified_at` of NULL so the row is re-classifiable on a future run.
- **CSV header mismatch:** ingest asserts exact expected header set. Mismatch → fail loudly with the diff. LinkedIn rarely changes the export format but if they do, we want to know.
- **Schema migrations:** linear, additive, numbered (`001_init.sql`, `002_*.sql`). `db.py` applies any in `migrations/` not yet in `_migrations`, in filename order. SQLite-friendly: no destructive ALTERs in v1.
- **Missing API keys:** CLI fails at startup with the specific env var that's missing and a pointer to `.env.example`.

## Testing

Unit tests around the parts where correctness matters; one optional integration test gated behind an env var.

- `test_normalize.py` — company-key normalization: case, whitespace, suffix stripping, punctuation collapse. ~15 cases.
- `test_ingest.py` — CSV → people rows, with the fixture CSV's edge cases (founder titles, multi-line positions, unicode names, missing email, missing company, empty cells, CRLF line endings, BOM).
- `test_classify_prompt.py` — given canned Haiku tool-use JSON, parse correctly. Verify enum defaults on garbage input.
- `test_idempotency.py` — run each stage twice on the same DB, assert second run is a no-op (no row counts change, no costs row added).
- `test_cost.py` — dry-run math matches actual cost-log sum on a tiny real run (gated env var).
- `tests/fixtures/tiny_connections.csv` — ~20 hand-picked rows covering the edge cases above. Committed.
- `tests/fixtures/fiber_canned_responses.json` — canned Fiber responses keyed by company name. Lets us run the full pipeline offline.

**Integration test** (one only, behind `RUN_LIVE_TESTS=1`): hits real Fiber on a 3-row fixture, asserts non-empty industry field on a well-known company. Not in CI by default. Sanity check for "did the API contract change?"

## Saved queries

In `saved_queries/` and committed to git. Each file is a normal SQL query against `people_enriched`. Either (a) `metadata.yml` references them by name + `sql_file`, or (b) the `datasette-query-files` plugin auto-discovers the directory — implementation can pick the simpler one. Either way, Datasette renders them as clickable canned queries with editable SQL.

Starter set:

- **`reconnect_targets.sql`** — connected ≥12mo ago, seniority IN ('Director','VP','C-suite','Founder'), role_bucket IN ('Sales','BD','Marketing'). Sorted by `connected_on` ascending (oldest first). Default for "who haven't I talked to in too long who matters?"
- **`sales_at_growth_companies.sql`** — role_bucket='Sales', funding_stage IN ('Series A','Series B','Series C+'), employee_band IN ('51-200','201-500'). Sorted by company.
- **`marketing_leaders.sql`** — role_bucket='Marketing', seniority IN ('VP','C-suite'). Sorted by industry.
- **`founders_in_target_industries.sql`** — role_bucket='Founder', industry IN ('SaaS','FinTech','HealthTech'). Replace industries list as targeting changes.

Users (you) edit these `.sql` files directly to tune per-business targeting. Diffs are reviewable. Datasette picks up changes on next page load.

## CLI shape

```
netcrm ingest <csv-path>                 # writes/updates people table
netcrm dedupe-companies                  # writes/updates companies table
netcrm enrich-companies [--dry-run] [--max-spend-usd N]
netcrm classify-people  [--dry-run] [--max-spend-usd N]
netcrm build-views                       # idempotent CREATE VIEW
netcrm run-all <csv-path>                # convenience: all stages in order
netcrm serve                             # invokes `datasette serve crm.db --metadata metadata.yml`
netcrm cost-report                       # SELECT FROM costs GROUP BY provider
```

Each sub-command is small and re-runnable. `run-all` is the happy path for first-time and refresh.

## Risks and open questions

- **Company-name normalization will misjoin some homonyms** (e.g. "Apple" the consultancy vs "Apple Inc"). Accepted tradeoff in v1; flag if any business-critical contacts get mis-enriched.
- **Fiber match rate.** Some companies (small consultancies, recently-renamed firms) won't be in Fiber's database. `fiber_status='not_found'` is expected for a long tail; queries should handle NULL industry gracefully.
- **Haiku misclassifies multilingual/non-English titles.** Acceptable for v1; user can manually correct by writing a `people_class_override` table later if needed.
- **The "post activity" question is parked.** If the user wants it later, the schema can grow a `posts` table keyed on `linkedin_url`; no other table changes.

## Acceptance criteria for v1

- [ ] Running `netcrm run-all ~/Downloads/connections.csv` on the user's real export produces `crm.db` with the people, companies, people_class tables and the people_enriched view populated.
- [ ] Total cost of a fresh run is logged and under $100 with the user's actual unique-company count (target: $30–60 expected).
- [ ] Re-running the same command with the same CSV does no API calls and adds zero rows.
- [ ] `datasette serve crm.db` renders the four starter saved queries, each returning a non-empty result set on the real data.
- [ ] Unit test suite passes; the integration test passes when run with `RUN_LIVE_TESTS=1`.
- [ ] README in the repo documents: install, env vars, first-run, re-run, saved-query workflow.
