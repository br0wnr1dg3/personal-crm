# Personal Network CRM

Turn your LinkedIn connections export into a queryable, enriched SQLite database. Slice it by role, seniority, industry, company size, or any combination — across all your businesses.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,serve]"
```

## Configure

Copy `.env.example` to `.env` and fill in:

- `FIBER_API_KEY` — Fiber AI API key (organization enrichment)
- `FIBER_API_BASE_URL` — usually `https://api.fiberai.com`
- `FIBER_USD_PER_CREDIT` — your tier's per-credit cost (default 0.020 = Prospector $300/15k credits)
- `ANTHROPIC_API_KEY` — Anthropic API key (Haiku classification)
- `ANTHROPIC_MODEL` — defaults to `claude-haiku-4-5-20251001`
- `NETCRM_DB_PATH` — defaults to `./crm.db`

## First run

Export your connections from LinkedIn → Settings → Data Privacy → Get a copy of your data → Connections. You'll get a `Connections.csv`.

```bash
# Dry-run first to see estimated cost
netcrm ingest ~/Downloads/connections.csv
netcrm dedupe-companies
netcrm enrich-companies --dry-run
netcrm classify-people --dry-run

# When the estimates look reasonable:
netcrm enrich-companies --max-spend-usd 100
netcrm classify-people --max-spend-usd 5
netcrm build-views

# Or all-in-one:
netcrm run-all ~/Downloads/connections.csv --max-spend-usd 105
```

## Browse + query

```bash
netcrm serve
```

Opens Datasette at <http://localhost:8001>. The starter saved queries appear at the top of the database page. Click one to run it; edit the SQL in-browser to tune; export as CSV.

## Re-running on a fresh CSV

In 3 months, when you re-export from LinkedIn:

```bash
netcrm run-all ~/Downloads/connections.csv
```

Only new people and new companies are enriched/classified. Cached results are reused. The cost-report tells you what the incremental run actually cost:

```bash
netcrm cost-report
```

## Editing saved queries

Files in `saved_queries/` are plain SQL. Edit them; Datasette picks up changes on next page load. Add new ones by dropping a `.sql` file in and adding an entry to `metadata.yml`.

## Schema

See `migrations/001_init.sql`. The view `people_enriched` is what you'll usually query — it joins people, companies, and people_class.

## Cost

A typical 4,800-connection run on the Prospector tier (~2,000 unique companies):

- Fiber org enrichment: ~$40
- Haiku classification: ~$0.20
- **Total: ~$40 one-time**

Re-runs on incremental CSVs cost only the deltas.
