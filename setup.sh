#!/usr/bin/env bash
# Personal Network CRM — one-time setup.
#
# Installs the Python package, prompts for API keys, creates the local
# database, and wires the MCP server into ~/.claude.json so Claude Code
# can talk to your network.
#
# Re-running this script is safe: it skips steps that are already done.
#
# Usage:
#   cd personal-network-crm
#   ./setup.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# Colors (skip if no tty)
if [ -t 1 ]; then
  C_BLUE=$'\033[34m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_OFF=""
fi

say()  { printf "%s%s%s\n"   "$C_BLUE"  "$*" "$C_OFF"; }
ok()   { printf "%s✓ %s%s\n" "$C_GREEN" "$*" "$C_OFF"; }
warn() { printf "%s! %s%s\n" "$C_YELLOW" "$*" "$C_OFF"; }
fail() { printf "%s✗ %s%s\n" "$C_RED"  "$*" "$C_OFF" >&2; exit 1; }

say "${C_BOLD}=== Personal Network CRM setup ===${C_OFF}"
echo "Repo: $REPO_ROOT"
echo

# ----- 1. Python version check -----
say "[1/6] Checking Python..."
PYTHON=""
for cand in python3.13 python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    VER="$("$cand" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    OK="$("$cand" -c 'import sys; print("ok" if sys.version_info >= (3,11) else "old")')"
    if [ "$OK" = "ok" ]; then
      PYTHON="$cand"
      ok "Using $cand ($VER)"
      break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  fail "No Python 3.11+ found. Install one and re-run:
    macOS:  brew install python@3.11
    Linux:  use pyenv or your package manager"
fi

# ----- 2. venv + install -----
say "[2/6] Creating venv and installing netcrm..."
if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -e ".[serve]"
ok "Installed netcrm + datasette into $REPO_ROOT/.venv"

# ----- 3. .env -----
say "[3/6] Configuring API keys..."
if [ -f .env ]; then
  warn ".env already exists — leaving it alone."
  warn "  If you want to reconfigure, delete .env and re-run."
else
  cp .env.example .env
  ok "Copied .env.example → .env"
fi

if grep -qE '^FIBER_API_KEY=$' .env; then
  warn "FIBER_API_KEY is empty in .env"
  echo "  Get yours at https://fiber.ai/ and paste it now (or leave blank to skip)."
  read -rp "  FIBER_API_KEY: " FIBER_KEY
  if [ -n "$FIBER_KEY" ]; then
    python3 -c "
import pathlib
p = pathlib.Path('.env'); t = p.read_text()
t = t.replace('FIBER_API_KEY=', 'FIBER_API_KEY=$FIBER_KEY')
p.write_text(t)
"
    ok "FIBER_API_KEY saved"
  fi
fi

if grep -qE '^ANTHROPIC_API_KEY=$' .env; then
  warn "ANTHROPIC_API_KEY is empty in .env"
  echo "  Get yours at https://console.anthropic.com/ and paste it now (or leave blank to skip)."
  read -rp "  ANTHROPIC_API_KEY: " ANTHROPIC_KEY
  if [ -n "$ANTHROPIC_KEY" ]; then
    python3 -c "
import pathlib
p = pathlib.Path('.env'); t = p.read_text()
t = t.replace('ANTHROPIC_API_KEY=', 'ANTHROPIC_API_KEY=$ANTHROPIC_KEY')
p.write_text(t)
"
    ok "ANTHROPIC_API_KEY saved"
  fi
fi

# ----- 4. Initialize DB (apply migrations) -----
say "[4/6] Initializing local database..."
python3 - <<EOF
from pathlib import Path
from netcrm import db
conn = db.connect("$REPO_ROOT/crm.db")
db.apply_migrations(conn, Path("$REPO_ROOT/migrations"))
print(f"  schema applied, db at $REPO_ROOT/crm.db")
EOF
ok "Database ready"

# ----- 5. Wire into ~/.claude.json (if present) -----
say "[5/6] Wiring MCP into Claude Code..."
CLAUDE_JSON="$HOME/.claude.json"
NETCRM_BIN="$REPO_ROOT/.venv/bin/netcrm-mcp"

if [ -f "$CLAUDE_JSON" ]; then
  TS="$(date +%Y%m%d-%H%M%S)"
  cp "$CLAUDE_JSON" "$CLAUDE_JSON.bak.$TS"
  python3 - <<EOF
import json
from pathlib import Path
p = Path("$CLAUDE_JSON")
data = json.loads(p.read_text())
data.setdefault("mcpServers", {})
existed = "netcrm" in data["mcpServers"]
data["mcpServers"]["netcrm"] = {
    "command": "$NETCRM_BIN",
    "env": {
        "NETCRM_DB_PATH": "$REPO_ROOT/crm.db",
    },
}
p.write_text(json.dumps(data, indent=2))
print("  updated" if existed else "  added")
print(f"  backup: {p}.bak.$TS")
EOF
  ok "Claude Code MCP entry wired"
  warn "RESTART Claude Code to pick up the new MCP server."
else
  warn "~/.claude.json not found — Claude Code may not be installed yet."
  echo "  When you install Claude Code, add this snippet under \"mcpServers\":"
  cat <<EOF
    "netcrm": {
      "command": "$NETCRM_BIN",
      "env": {
        "NETCRM_DB_PATH": "$REPO_ROOT/crm.db"
      }
    }
EOF
  echo
  echo "  For claude.ai (web), Cursor, or Zed, use the equivalent MCP config UI."
fi

# ----- 6. Done -----
say "[6/6] Setup complete!"
echo
echo "${C_BOLD}Next steps:${C_OFF}"
echo "  1. Export your LinkedIn connections from"
echo "     https://www.linkedin.com/mypreferences/d/download-my-data"
echo "     Select 'Connections' → wait ~10min → download → unzip → 'Connections.csv'."
echo
echo "  2. Restart Claude Code, then in any new conversation:"
echo "     ${C_GREEN}> Use the netcrm MCP server. Call pipeline_status, then ingest_csv at ~/Downloads/Connections.csv${C_OFF}"
echo
echo "  3. The agent will walk you through dedupe → classify → enrich → query."
echo
echo "Cost expectation for a typical ~5,000-contact CSV:"
echo "  - Classification (Anthropic Haiku):     ~\$1-2"
echo "  - Company enrichment (Fiber AI):         \$0-80 depending on scope you pick"
echo
echo "${C_BOLD}Data privacy:${C_OFF} Everything stays on your machine. Nothing is sent anywhere"
echo "except the network calls TO Fiber and Anthropic (with YOUR keys, for YOUR data)."
