---
description: One-time setup for Personal Network CRM — installs the package, prompts for API keys, initializes the local DB, and wires the MCP server into ~/.claude.json so Claude Code can chat with your network.
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion
---

# Personal Network CRM — Setup

You are walking the user through one-time setup of **netcrm**, a local Python tool that turns their LinkedIn `Connections.csv` export into a queryable, agent-driven personal CRM. The user has just cloned the repo and is running `/personal-crm-setup` inside Claude Code from the repo root.

Be friendly but efficient. Don't over-narrate — just do the work, surface any decisions the user needs to make, and keep moving.

## Working directory check

First, confirm you're in the netcrm repo root. The cwd should contain `pyproject.toml`, `netcrm/`, `migrations/`, and `setup.sh`. If not, tell the user to `cd` into the cloned `personal-network-crm` directory and re-run.

```bash
test -f pyproject.toml && test -d netcrm && test -d migrations && echo "ok: in repo root" || echo "ERROR: not in repo root"
```

## Step 1: Python check

Find Python 3.11+. Try in order: `python3.13`, `python3.12`, `python3.11`, `python3`. For each, run:

```bash
<cand> -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")'
```

Use the first one that's ≥ 3.11. Remember it as `$PY` for the next step.

If none found, stop and tell the user:
> No Python 3.11+ found. Install one and re-run this command:
> - macOS: `brew install python@3.11`
> - Linux: use pyenv or your package manager

## Step 2: Create venv + install

If `.venv/` doesn't already exist, create it:

```bash
$PY -m venv .venv
```

Then install netcrm in editable mode with the `serve` extra (for Datasette, optional but lightweight):

```bash
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[serve]"
```

Confirm it worked:

```bash
.venv/bin/netcrm --help | head -5
.venv/bin/netcrm-mcp --help 2>&1 | head -3 || true   # MCP stdio servers don't have --help but the binary should exist
test -x .venv/bin/netcrm-mcp && echo "netcrm-mcp installed"
```

## Step 3: API keys

Check whether `.env` already exists:

```bash
test -f .env && echo "exists" || echo "missing"
```

If it doesn't exist, create a fresh one with this content (write the file, then we'll fill in keys):

```
# Fiber AI organization + contact enrichment
FIBER_API_KEY=
FIBER_USD_PER_CREDIT=0.020

# Anthropic for Haiku classification
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-haiku-4-5-20251001

# Database (resolved relative to repo root if relative)
NETCRM_DB_PATH=crm.db
```

Then check whether `FIBER_API_KEY` or `ANTHROPIC_API_KEY` are blank in `.env` (use `grep -E '^FIBER_API_KEY=$' .env`).

For each blank key, use **AskUserQuestion** to prompt the user. Phrase it like:

> **Anthropic API key** — needed for classification (~$1–2 per 5,000 contacts).
> Get yours at https://console.anthropic.com/ and paste it below.
> If you don't have one yet, choose "skip for now" and you can add it later by editing `.env`.

Then for Fiber:

> **Fiber AI API key** — needed for company + email enrichment (cost depends on scope, ~$0.04/contact for emails, ~$0.08/company).
> Get yours at https://fiber.ai/ (Prospector plan or higher).
> If you don't have one yet, choose "skip for now" — you can run classification + basic queries without it.

Whatever the user provides, use **Edit** to update `.env` with the actual keys. (Don't echo the keys back in your text response.)

## Step 4: Initialize the database

Apply migrations to create the SQLite schema. This is a no-op if already applied.

```bash
.venv/bin/python -c "
from pathlib import Path
from netcrm import db
import os
db_path = os.environ.get('NETCRM_DB_PATH','crm.db')
conn = db.connect(db_path)
db.apply_migrations(conn, Path('migrations'))
print(f'schema applied at {db_path}')
"
```

## Step 5: Wire netcrm into ~/.claude.json

This is the moment that makes the agent available to chat. We always make a backup first.

```bash
test -f ~/.claude.json && cp ~/.claude.json ~/.claude.json.bak.$(date +%Y%m%d-%H%M%S) && echo "backup made" || echo "no claude.json yet (Claude Code may not be set up — see fallback below)"
```

If `~/.claude.json` exists, add (or update) the `netcrm` MCP server entry:

```bash
REPO_ROOT="$(pwd)"
.venv/bin/python <<EOF
import json
from pathlib import Path
p = Path.home() / ".claude.json"
data = json.loads(p.read_text())
data.setdefault("mcpServers", {})
data["mcpServers"]["netcrm"] = {
    "command": "$REPO_ROOT/.venv/bin/netcrm-mcp",
    "env": {
        "NETCRM_DB_PATH": "$REPO_ROOT/crm.db",
    },
}
p.write_text(json.dumps(data, indent=2))
print("netcrm MCP entry written")
EOF
```

If `~/.claude.json` does **not** exist (rare; means Claude Code hasn't been set up yet), give the user the snippet to paste manually:

```json
"netcrm": {
  "command": "<repo_root>/.venv/bin/netcrm-mcp",
  "env": {
    "NETCRM_DB_PATH": "<repo_root>/crm.db"
  }
}
```

## Step 6: Final report

Print a clean summary to the user:

```
✓ Setup complete!

What just happened:
  • Python 3.x venv at .venv/
  • netcrm + datasette installed
  • .env created/updated with your API keys
  • Local database initialized at crm.db
  • MCP server wired into ~/.claude.json (backed up to ~/.claude.json.bak.<ts>)

Next steps:
  1. Export your LinkedIn connections:
     https://www.linkedin.com/mypreferences/d/download-my-data
     Select 'Connections' → wait ~10min → download → unzip → 'Connections.csv'

  2. RESTART Claude Code (Cmd-Q or exit, then `claude` again).
     This is critical — Claude Code only loads MCP servers at startup.

  3. In any new conversation, say:
     > Use the netcrm MCP server. Call pipeline_status, then ingest_csv at ~/Downloads/Connections.csv

  4. The agent will walk you through: dedupe → classify (~$1-2)
     → enrich companies (you pick scope) → query/tag/reveal emails.

Privacy reminder: your contacts stay on your machine. Network calls go ONLY to
Fiber and Anthropic (with YOUR keys, for YOUR data). Nothing is uploaded anywhere else.
```

## Edge cases to handle gracefully

- **User re-runs the command** — every step should be idempotent. Skip steps already done; report what was preserved.
- **User skipped a key earlier and now wants to add it** — they can just re-run `/personal-crm-setup` and you'll detect the blank values in `.env` and prompt again.
- **User has a broken venv from a prior attempt** — if `.venv/bin/netcrm-mcp` doesn't exist after install, remove `.venv/` (`rm -rf .venv`) and start step 2 over. Ask permission before deleting.

## Do not

- **Do not** echo API keys back in user-visible text.
- **Do not** modify any file other than `.env`, `crm.db` (via migrations), and `~/.claude.json` (with backup).
- **Do not** install global Python packages — everything goes in the project's `.venv/`.
- **Do not** skip the backup of `~/.claude.json`.
