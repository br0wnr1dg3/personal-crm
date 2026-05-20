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

Two ways to interact with the dataset.

### Datasette (grid + SQL panel)

```bash
netcrm serve
```

Opens Datasette at <http://localhost:8001>. The starter saved queries appear at the top of the database page. Click one to run it; edit the SQL in-browser to tune; export as CSV.

### Chat agent via MCP (recommended)

`netcrm-mcp` is a stdio MCP server that exposes 12 tools — query, tag, note, mark outreached, trigger more Fiber enrichment, check credit balance. Wire it into any MCP-aware chat client (Claude Code, claude.ai with custom integration, Cursor, Zed) and talk to your network like an agent.

**Claude Code wiring** — add to `~/.claude.json` (or the equivalent for your install):

```json
{
  "mcpServers": {
    "netcrm": {
      "command": "/full/path/to/personal-network-crm/.venv/bin/netcrm-mcp",
      "env": {
        "NETCRM_DB_PATH": "/full/path/to/personal-network-crm/crm.db",
        "FIBER_API_KEY": "sk_live_...",
        "FIBER_USD_PER_CREDIT": "0.020"
      }
    }
  }
}
```

Restart Claude Code, then in chat:

```
> Show me VPs of marketing in software companies I've connected to in the last year
> Tag Alice Smith 'demo-scheduled' and add a note that Jane introduced us
> What would it cost to enrich every Engineering Director's company I haven't enriched yet?
> Enrich those 50 companies, dry_run=false
> Who haven't I reached out to in 6 months who's a Founder at a SaaS company?
```

The agent writes its own SQL through `query_contacts`, persists state via `add_tag`/`set_note`/`mark_outreached`, and can trigger more Fiber enrichment via `enrich_companies` with cost guardrails (always defaults to dry-run).

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
