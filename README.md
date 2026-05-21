# Personal Network CRM

Turn your LinkedIn connections into a queryable, enriched personal CRM you can chat with from Claude Code.

```
You:    Show me my marketing VPs in software companies, sorted by when we last spoke.
Claude: [calls query_contacts() → returns 14 people]
        Will Burns (VP Marketing, EchoStar Corporation) — 9 mo
        Rae Cline   (VP Marketing, AEG Presents)        — 13 mo
        ...

You:    Tag Will "demo-scheduled" and note "intro from Jane in March."
Claude: [calls add_tag + append_note]  Done.

You:    Enrich every Founder I haven't enriched yet — what would that cost?
Claude: [calls count_enrichment_scope]  888 companies, ~$71. Want me to start
        with just the GTM-senior ones for ~$18, or go ahead with all of them?
```

Everything runs **locally** on your machine. Your contacts never leave your laptop except for the API calls to Fiber (company enrichment) and Anthropic (Haiku for role classification) — using **your own keys**.

---

## Setup (5 minutes)

Recommended path — let Claude walk you through it:

```bash
git clone https://github.com/YOUR_USERNAME/personal-network-crm.git
cd personal-network-crm
claude                          # opens Claude Code in this directory
> /personal-crm-setup           # paste this slash command in chat
```

Claude will:

1. Find a Python 3.11+ on your machine
2. Create a venv and install netcrm
3. Prompt you for your Fiber and Anthropic API keys (you can skip either; add later by editing `.env`)
4. Initialize the local SQLite database
5. Wire the MCP server into `~/.claude.json` (with a backup)
6. Tell you exactly what to do next

Then **restart Claude Code** (Cmd-Q + reopen, or exit + `claude` again) so it picks up the new MCP server.

### Alternative: shell script

Don't have Claude Code yet, or prefer a one-liner? `./setup.sh` does the same thing non-interactively (prompts for keys via stdin). The slash command is friendlier; the script is fine for CI or scripted installs.

### Getting the API keys

- **Anthropic (required):** [console.anthropic.com](https://console.anthropic.com/) — ~$1–2 for a 5,000-contact CSV. You'll need a payment method on file.
- **Fiber AI (recommended):** [fiber.ai](https://fiber.ai/) — company enrichment (industry, headcount, location) + email/phone reveal. Prospector plan is $300/15k credits ≈ $0.02/credit. You can skip this for classification-only, but the ICP queries and email-reveal tool need it.

### Getting your LinkedIn CSV

LinkedIn → Settings → Data Privacy → [Get a copy of your data](https://www.linkedin.com/mypreferences/d/download-my-data) → select **Connections** → wait ~10 minutes → download → unzip → there's your `Connections.csv`.

---

## First conversation

Restart Claude Code, then in any new chat:

> Use the netcrm MCP server. Call pipeline_status to see what's there, then ingest my CSV at `~/Downloads/Connections.csv`.

The agent will:

1. Run `pipeline_status` → see an empty DB
2. Run `ingest_csv` → load all your contacts (free, ~1 second)
3. Run `dedupe_companies` → roll up to unique companies (free)
4. Run `classify_people` with `dry_run=true` → show estimated cost (~$1–2)
5. Ask you to confirm the spend
6. Run `classify_people` with `dry_run=false` and `max_spend_usd=5` → 2–3 minutes

That gets you queryable role/seniority for everyone. Then ask:

> What would it cost to enrich every Founder's company that I haven't enriched yet?

The agent will show you scope + cost, and propose narrower slices if it's expensive.

---

## What the agent can do (19 tools)

| Category | Tools |
|---|---|
| **Orient** | `pipeline_status`, `list_schema`, `get_credit_balance` |
| **Pipeline** | `ingest_csv`, `dedupe_companies`, `classify_people`, `build_views` |
| **Query** | `query_contacts` (read-only SQL), `get_contact` |
| **Tag/note** | `add_tag`, `remove_tag`, `list_tags`, `set_note`, `append_note`, `mark_outreached` |
| **Company enrich** | `count_enrichment_scope`, `enrich_companies` (dry-run by default, hard spend caps) |
| **Person reveal** | `reveal_contact`, `reveal_contacts_by_filter` (work + personal email, optional phone) |

Mutations go through narrow tool surfaces. Read queries can be any `SELECT` — the agent is good at writing the SQL itself.

---

## Optional: Datasette web UI

If you want a grid + SQL panel instead of chat:

```bash
source .venv/bin/activate
netcrm serve
```

Opens [http://localhost:8001](http://localhost:8001). The 4 starter saved queries appear at the top.

---

## Privacy & data handling

- **Local-only.** Your contacts are in `crm.db` in this directory. Nothing leaves your machine except the API calls TO Fiber and Anthropic.
- **No telemetry.** This tool doesn't phone home.
- **Bring your own keys.** You pay Fiber/Anthropic directly. We never see your data or your keys.
- **`.env` and `crm.db` are gitignored.** If you fork the repo and push, your secrets and data won't go with it.

If you want to delete everything: `rm -rf crm.db .env` and remove the `netcrm` entry from `~/.claude.json`.

---

## Cost expectations

For a typical 5,000-contact CSV:

- **Classification (Anthropic Haiku):** $1–2 one-time. Re-runs only pay for new contacts.
- **Company enrichment (Fiber):** Highly scope-dependent. ~$0.08 per company. Choose what to enrich:
  - **All companies** (~2,000–3,500 unique): $160–280
  - **Senior GTM only** (~1,500): $120
  - **Recent + senior + GTM** (~250): $20
- **Email reveal (Fiber):** ~$0.04 per person for work + personal email. Bulk by filter, idempotent — re-asking for the same person is free (cached).
  - 100 senior outreach targets: ~$4
  - 1,000 contacts: ~$40
- The agent will always show estimates before spending, and you can set `max_spend_usd` caps on any run.

---

## Re-running on a fresh CSV

When LinkedIn ships you a new export in a few months:

> Ingest my new CSV at `~/Downloads/Connections.csv`. Only run classify and enrich on the deltas.

Idempotency is baked in — the pipeline skips contacts and companies already processed. You pay only for the new ones.

---

## Troubleshooting

**"netcrm MCP tools aren't showing up in Claude Code"** — Did you restart Claude Code after running setup? Check `~/.claude.json` has `"netcrm"` under `mcpServers`.

**"Fiber enrichment returns lots of `not_found`"** — Some of your contacts have moved companies and their current LinkedIn profile no longer lists the company you have in your CSV. That's expected; 5–15% miss rate is normal.

**"My ingest_csv failed with 'unexpected CSV headers'"** — Make sure you exported just **Connections** from LinkedIn (not the full data archive). The expected headers are: First Name, Last Name, URL, Email Address, Company, Position, Connected On.

**"Spend cap was hit but I want to finish"** — Just re-run the same command with a higher cap. The pipeline is resume-safe; only un-processed rows get billed.

---

## Architecture

- `netcrm/` — Python package: ingest, normalize, classify, enrich, MCP server, CLI
- `migrations/` — SQLite schema migrations (linear, additive)
- `saved_queries/` — Plain SQL files Datasette exposes as canned queries
- `scripts/enrich_via_fiber_mcp.py` — Standalone enrichment script (the agent calls equivalent logic via the MCP tools)
- `tests/` — pytest suite

See `docs/superpowers/specs/` and `docs/superpowers/plans/` for the design + implementation history.

---

## Contributing

PRs welcome. Run `pytest -v` before submitting. The codebase is small (~2k LOC) and deliberately boring — typer CLI, sqlite3, httpx, the official Anthropic and MCP SDKs.
