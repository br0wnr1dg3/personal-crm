"""MCP server exposing netcrm as a set of tools.

Run via stdio: ``python -m netcrm.mcp_server`` (or via the entrypoint
in pyproject.toml, ``netcrm-mcp``).

Wire into Claude Code / claude.ai / Cursor with a config like::

    {
      "mcpServers": {
        "netcrm": {
          "command": "/full/path/to/.venv/bin/python",
          "args": ["-m", "netcrm.mcp_server"],
          "env": {
            "NETCRM_DB_PATH": "/full/path/to/crm.db",
            "FIBER_API_KEY": "sk_live_...",
            "FIBER_USD_PER_CREDIT": "0.020"
          }
        }
      }
    }

Then in chat::

    "Show me senior marketing leaders in software"
    "Tag Alice Smith 'demo-scheduled'"
    "Enrich every Engineering Director I haven't enriched yet"
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load .env from the repo root explicitly. When Claude Code spawns this
# server via stdio the cwd is whatever Claude Code's cwd is, NOT the repo —
# so default load_dotenv() can't find the file.
REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=REPO_ROOT / ".env")

_db_env = os.environ.get("NETCRM_DB_PATH")
DB_PATH = (
    Path(_db_env).expanduser()
    if _db_env and Path(_db_env).expanduser().is_absolute()
    else (REPO_ROOT / (_db_env or "crm.db")).resolve()
)
USD_PER_CREDIT = float(os.environ.get("FIBER_USD_PER_CREDIT", "0.020"))
FIBER_API_KEY = os.environ.get("FIBER_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
FIBER_URL = "https://mcp.fiber.ai/mcp/v2"
MIGRATIONS_DIR = REPO_ROOT / "migrations"

mcp = FastMCP("netcrm")


# ---------- DB helpers ----------

def _ro_conn() -> sqlite3.Connection:
    """Read-only connection (URI mode), so query_contacts cannot mutate."""
    uri = f"file:{DB_PATH.absolute()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rw_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows_to_jsonable(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# Allowed top-level SQL keywords for read-only queries.
_READONLY_SQL_RE = re.compile(
    r"^\s*(WITH|SELECT|EXPLAIN)\b",
    flags=re.IGNORECASE,
)
_DANGER_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|REPLACE|TRUNCATE|VACUUM|PRAGMA)\b",
    flags=re.IGNORECASE,
)


def _validate_readonly_sql(sql: str) -> None:
    if not _READONLY_SQL_RE.match(sql):
        raise ValueError("SQL must start with SELECT, WITH, or EXPLAIN")
    if _DANGER_RE.search(sql):
        raise ValueError(
            "SQL contains a write/DDL keyword; use the tag/note/outreach "
            "tools to mutate state"
        )


# ---------- Schema discovery ----------

@mcp.tool()
def list_schema() -> str:
    """Describe the queryable schema so the agent can write correct SQL.

    Returns columns of the people_enriched view, enum values for role_bucket
    / seniority, top 30 distinct industry values from the enriched data, and
    a few example queries.
    """
    conn = _ro_conn()
    cols = conn.execute("PRAGMA table_info(people_enriched)").fetchall()
    role_buckets = sorted(
        r[0] for r in conn.execute(
            "SELECT DISTINCT role_bucket FROM people_class"
        ).fetchall()
    )
    seniorities = sorted(
        r[0] for r in conn.execute(
            "SELECT DISTINCT seniority FROM people_class"
        ).fetchall()
    )
    industries = [
        {"industry": r[0], "n_companies": r[1]}
        for r in conn.execute(
            "SELECT industry, COUNT(*) FROM companies "
            "WHERE industry IS NOT NULL GROUP BY industry ORDER BY 2 DESC LIMIT 30"
        ).fetchall()
    ]
    employee_bands = sorted(
        r[0] for r in conn.execute(
            "SELECT DISTINCT employee_band FROM companies WHERE employee_band IS NOT NULL"
        ).fetchall()
    )
    out = {
        "view": "people_enriched",
        "columns": [
            {"name": c["name"], "type": c["type"], "notnull": bool(c["notnull"])}
            for c in cols
        ],
        "enums": {
            "role_bucket": role_buckets,
            "seniority": seniorities,
            "employee_band": employee_bands,
        },
        "top_industries": industries,
        "notes": [
            "fiber_status='ok' means the company is enriched; NULL/'not_found' = not yet",
            "tags is a comma-separated string; use 'tags LIKE \"%name%\"' to filter",
            "connected_on is a DATE; use date('now', '-N months') for relative filters",
            "last_outreach_at is a TIMESTAMP set by mark_outreached",
        ],
        "examples": [
            "SELECT * FROM people_enriched WHERE role_bucket='Marketing' AND seniority IN ('VP','C-suite') LIMIT 20",
            "SELECT first_name, last_name, company_name FROM people_enriched WHERE industry='Software Development' AND role_bucket='Founder'",
            "SELECT first_name, last_name FROM people_enriched WHERE connected_on <= date('now','-12 months') AND seniority='VP'",
            "SELECT first_name, last_name, tags FROM people_enriched WHERE tags LIKE '%warm-intro%'",
        ],
    }
    conn.close()
    return json.dumps(out, indent=2, default=str)


# ---------- Read tools ----------

@mcp.tool()
def query_contacts(sql: str, limit: int = 100) -> str:
    """Run a read-only SQL query against the people_enriched view.

    Args:
        sql: A SELECT/WITH/EXPLAIN query. Mutating keywords are rejected.
        limit: Hard cap on rows returned (default 100, max 1000). Applied
            on top of any LIMIT in the SQL itself.
    """
    _validate_readonly_sql(sql)
    limit = max(1, min(limit, 1000))
    conn = _ro_conn()
    try:
        rows = conn.execute(sql).fetchmany(limit)
        return json.dumps({
            "count": len(rows),
            "limit_applied": limit,
            "rows": _rows_to_jsonable(rows),
        }, indent=2, default=str)
    finally:
        conn.close()


@mcp.tool()
def get_contact(linkedin_url: str) -> str:
    """Fetch one contact's full enriched record by LinkedIn URL."""
    conn = _ro_conn()
    try:
        row = conn.execute(
            "SELECT * FROM people_enriched WHERE linkedin_url = ?",
            (linkedin_url,),
        ).fetchone()
        if not row:
            return json.dumps({"error": "not found", "linkedin_url": linkedin_url})
        return json.dumps(dict(row), indent=2, default=str)
    finally:
        conn.close()


# ---------- Tag / note / outreach mutation ----------

def _ensure_state_row(conn: sqlite3.Connection, linkedin_url: str) -> None:
    """Create a contact_state row if missing (idempotent)."""
    conn.execute(
        "INSERT OR IGNORE INTO contact_state(linkedin_url, updated_at)"
        " VALUES (?, ?)",
        (linkedin_url, _now()),
    )


def _current_tags(conn: sqlite3.Connection, linkedin_url: str) -> list[str]:
    row = conn.execute(
        "SELECT tags FROM contact_state WHERE linkedin_url=?",
        (linkedin_url,),
    ).fetchone()
    if not row or not row["tags"]:
        return []
    return [t.strip() for t in row["tags"].split(",") if t.strip()]


@mcp.tool()
def add_tag(linkedin_url: str, tag: str) -> str:
    """Add a tag to a contact. Tags are lowercased + deduped automatically.

    Examples: "demo-scheduled", "warm-intro-via-jane", "skip", "follow-up-q3".
    """
    tag = tag.strip().lower()
    if not tag:
        return json.dumps({"error": "empty tag"})
    with _rw_conn() as conn:
        # Confirm the contact exists
        if not conn.execute(
            "SELECT 1 FROM people WHERE linkedin_url=?", (linkedin_url,)
        ).fetchone():
            return json.dumps({"error": "unknown linkedin_url", "linkedin_url": linkedin_url})
        _ensure_state_row(conn, linkedin_url)
        tags = set(_current_tags(conn, linkedin_url))
        tags.add(tag)
        conn.execute(
            "UPDATE contact_state SET tags=?, updated_at=? WHERE linkedin_url=?",
            (",".join(sorted(tags)), _now(), linkedin_url),
        )
    return json.dumps({"ok": True, "linkedin_url": linkedin_url, "tags": sorted(tags)})


@mcp.tool()
def remove_tag(linkedin_url: str, tag: str) -> str:
    """Remove a tag from a contact (no-op if not present)."""
    tag = tag.strip().lower()
    with _rw_conn() as conn:
        tags = set(_current_tags(conn, linkedin_url))
        tags.discard(tag)
        new_tags = ",".join(sorted(tags)) if tags else None
        conn.execute(
            "UPDATE contact_state SET tags=?, updated_at=? WHERE linkedin_url=?",
            (new_tags, _now(), linkedin_url),
        )
    return json.dumps({"ok": True, "linkedin_url": linkedin_url, "tags": sorted(tags)})


@mcp.tool()
def list_tags() -> str:
    """Return all distinct tags with a count of how many contacts have each."""
    conn = _ro_conn()
    try:
        # tags is a comma-separated string; explode it via a UNION ALL hack
        # using SQLite recursive CTE
        rows = conn.execute("""
            WITH RECURSIVE split(tag, rest) AS (
              SELECT '', tags || ',' FROM contact_state WHERE tags IS NOT NULL AND tags != ''
              UNION ALL
              SELECT
                substr(rest, 1, instr(rest, ',') - 1),
                substr(rest, instr(rest, ',') + 1)
              FROM split
              WHERE rest != ''
            )
            SELECT tag, COUNT(*) AS n
            FROM split
            WHERE tag != ''
            GROUP BY tag
            ORDER BY n DESC, tag
        """).fetchall()
        return json.dumps({"tags": [{"tag": r["tag"], "count": r["n"]} for r in rows]}, indent=2)
    finally:
        conn.close()


@mcp.tool()
def set_note(linkedin_url: str, note: str) -> str:
    """Replace the freeform note on a contact (overwrites prior note)."""
    with _rw_conn() as conn:
        if not conn.execute(
            "SELECT 1 FROM people WHERE linkedin_url=?", (linkedin_url,)
        ).fetchone():
            return json.dumps({"error": "unknown linkedin_url", "linkedin_url": linkedin_url})
        _ensure_state_row(conn, linkedin_url)
        conn.execute(
            "UPDATE contact_state SET notes=?, updated_at=? WHERE linkedin_url=?",
            (note, _now(), linkedin_url),
        )
    return json.dumps({"ok": True, "linkedin_url": linkedin_url, "note_set": True})


@mcp.tool()
def append_note(linkedin_url: str, note: str) -> str:
    """Append a line to the freeform note (timestamps each entry)."""
    stamp = _now()
    line = f"[{stamp}] {note.strip()}"
    with _rw_conn() as conn:
        if not conn.execute(
            "SELECT 1 FROM people WHERE linkedin_url=?", (linkedin_url,)
        ).fetchone():
            return json.dumps({"error": "unknown linkedin_url", "linkedin_url": linkedin_url})
        _ensure_state_row(conn, linkedin_url)
        existing = conn.execute(
            "SELECT notes FROM contact_state WHERE linkedin_url=?", (linkedin_url,)
        ).fetchone()["notes"]
        combined = (existing + "\n" + line) if existing else line
        conn.execute(
            "UPDATE contact_state SET notes=?, updated_at=? WHERE linkedin_url=?",
            (combined, stamp, linkedin_url),
        )
    return json.dumps({"ok": True, "linkedin_url": linkedin_url, "appended": line})


@mcp.tool()
def mark_outreached(linkedin_url: str, when: str | None = None) -> str:
    """Record an outreach event. ``when`` is ISO-8601; defaults to now()."""
    ts = when or _now()
    with _rw_conn() as conn:
        if not conn.execute(
            "SELECT 1 FROM people WHERE linkedin_url=?", (linkedin_url,)
        ).fetchone():
            return json.dumps({"error": "unknown linkedin_url", "linkedin_url": linkedin_url})
        _ensure_state_row(conn, linkedin_url)
        conn.execute(
            "UPDATE contact_state SET last_outreach_at=?, updated_at=? WHERE linkedin_url=?",
            (ts, _now(), linkedin_url),
        )
    return json.dumps({"ok": True, "linkedin_url": linkedin_url, "last_outreach_at": ts})


# ---------- Enrichment-on-demand ----------

@mcp.tool()
def count_enrichment_scope(filter_sql: str | None = None) -> str:
    """How many UN-ENRICHED companies match the filter? Used to preview cost.

    ``filter_sql`` is the body of a WHERE clause against people_enriched,
    e.g. "role_bucket = 'Founder' AND seniority IN ('VP','C-suite')".
    Pass None to count every un-enriched company in the DB.

    Returns counts + estimated cost (4 credits/company × USD per credit).
    """
    _safe_filter = filter_sql or "1=1"
    if _DANGER_RE.search(_safe_filter):
        return json.dumps({"error": "filter contains write/DDL keywords"})
    conn = _ro_conn()
    try:
        sql = f"""
            SELECT COUNT(DISTINCT pe.company_key) AS n
            FROM people_enriched pe
            JOIN companies c ON c.company_key = pe.company_key
            WHERE pe.company_key IS NOT NULL
              AND c.fiber_enriched_at IS NULL
              AND ({_safe_filter})
        """
        n = conn.execute(sql).fetchone()[0]
    finally:
        conn.close()
    est = n * 4 * USD_PER_CREDIT
    return json.dumps({
        "unenriched_companies_matching": n,
        "estimated_credits": n * 4,
        "estimated_usd": round(est, 2),
        "per_company_usd": round(4 * USD_PER_CREDIT, 4),
    })


@mcp.tool()
async def enrich_companies(
    filter_sql: str | None = None,
    max_companies: int = 50,
    dry_run: bool = True,
) -> str:
    """Trigger Fiber enrichment on companies matching ``filter_sql``.

    Args:
        filter_sql: WHERE clause body against people_enriched
            (e.g. "role_bucket='Founder' AND industry IS NULL").
            Pass None to target every un-enriched company.
        max_companies: Hard cap on companies to enrich in this call
            (default 50, max 500). Acts as a budget guardrail.
        dry_run: When True (default), returns scope + cost estimate
            WITHOUT calling Fiber. Set False to actually spend credits.

    The chain per company: profileLiveEnrich(senior-most contact's LinkedIn URL)
    → extract LinkedIn company slug → companyLiveEnrich(slug).
    Costs 4 credits ≈ $0.08 per company.
    """
    if not FIBER_API_KEY:
        return json.dumps({"error": "FIBER_API_KEY not set"})
    _safe_filter = filter_sql or "1=1"
    if _DANGER_RE.search(_safe_filter):
        return json.dumps({"error": "filter contains write/DDL keywords"})
    max_companies = max(1, min(max_companies, 500))

    # Build the same senior-most-per-company query used by the standalone script
    target_sql = f"""
        WITH ranked AS (
          SELECT
            c.company_key, c.display_name, p.linkedin_url,
            p.first_name, p.last_name,
            pc.role_bucket, pc.seniority,
            ROW_NUMBER() OVER (
              PARTITION BY c.company_key
              ORDER BY
                CASE pc.seniority
                  WHEN 'Founder'  THEN 1
                  WHEN 'C-suite'  THEN 2
                  WHEN 'VP'       THEN 3
                  WHEN 'Director' THEN 4
                  WHEN 'Manager'  THEN 5
                  ELSE 6
                END,
                p.connected_on DESC
            ) AS rk
          FROM companies c
          JOIN people p ON p.company_key = c.company_key
          JOIN people_class pc ON pc.linkedin_url = p.linkedin_url
          JOIN people_enriched pe ON pe.linkedin_url = p.linkedin_url
          WHERE c.fiber_enriched_at IS NULL
            AND ({_safe_filter})
        )
        SELECT company_key, display_name, linkedin_url, first_name, last_name, role_bucket, seniority
        FROM ranked WHERE rk = 1
        ORDER BY company_key
        LIMIT {max_companies}
    """
    conn = _rw_conn()
    targets = conn.execute(target_sql).fetchall()
    estimate_usd = round(len(targets) * 4 * USD_PER_CREDIT, 2)

    if dry_run or not targets:
        conn.close()
        return json.dumps({
            "dry_run": dry_run,
            "targets": len(targets),
            "estimated_usd": estimate_usd,
            "first_5_targets": [
                {"company": t["display_name"], "person": f"{t['first_name']} {t['last_name']}",
                 "role": t["role_bucket"], "seniority": t["seniority"]}
                for t in targets[:5]
            ],
            "hint": "set dry_run=false to actually run the enrichment",
        })

    # Real run: delegate to the script's mappers via direct import.
    from scripts.enrich_via_fiber_mcp import (  # type: ignore[import-not-found]
        extract_slug_for_company, log_cost, map_company_fields,
    )
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {FIBER_API_KEY}"}
    ok = not_found = errors = 0

    async with streamablehttp_client(FIBER_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for t in targets:
                ck, disp, purl = t["company_key"], t["display_name"], t["linkedin_url"]
                # profileLiveEnrich
                try:
                    pr = await session.call_tool("profileLiveEnrich_tool", {
                        "identifier": purl, "getDetailedWorkExperience": True,
                    })
                    pp = json.loads(pr.content[0].text)
                except Exception:
                    conn.execute(
                        "UPDATE companies SET fiber_status='error' WHERE company_key=?",
                        (ck,),
                    )
                    conn.commit()
                    errors += 1
                    continue
                if pp.get("status") != 200:
                    errors += 1
                    continue
                p_credits = (pp.get("data", {}).get("chargeInfo") or {}).get("creditsCharged") or 0
                log_cost(conn, "fiber", "profile_live_enrich",
                         p_credits, p_credits * USD_PER_CREDIT, ck)
                slug, _ = extract_slug_for_company(pp, disp, ck)
                if not slug:
                    conn.execute(
                        "UPDATE companies SET fiber_status='not_found', fiber_enriched_at=? "
                        "WHERE company_key=?",
                        (_now(), ck),
                    )
                    conn.commit()
                    not_found += 1
                    continue
                # companyLiveEnrich
                try:
                    cr = await session.call_tool("companyLiveEnrich_tool", {
                        "type": "slug", "value": slug,
                    })
                    cp = json.loads(cr.content[0].text)
                except Exception:
                    conn.execute(
                        "UPDATE companies SET fiber_status='error' WHERE company_key=?",
                        (ck,),
                    )
                    conn.commit()
                    errors += 1
                    continue
                if cp.get("status") != 200:
                    errors += 1
                    continue
                c_credits = (cp.get("data", {}).get("chargeInfo") or {}).get("creditsCharged") or 0
                log_cost(conn, "fiber", "company_live_enrich",
                         c_credits, c_credits * USD_PER_CREDIT, ck)
                company = (cp.get("data", {}).get("output") or {}).get("company") or {}
                fields = map_company_fields(company)
                fields["fiber_enriched_at"] = _now()
                fields["fiber_status"] = "ok"
                fields["company_key"] = ck
                conn.execute(
                    """
                    UPDATE companies SET
                      industry=:industry, sub_industry=:sub_industry,
                      employee_band=:employee_band, revenue_band=:revenue_band,
                      funding_stage=:funding_stage,
                      hq_country=:hq_country, hq_region=:hq_region,
                      website=:website, description=:description,
                      fiber_enriched_at=:fiber_enriched_at,
                      fiber_status=:fiber_status
                    WHERE company_key=:company_key
                    """,
                    fields,
                )
                conn.commit()
                ok += 1
    total_spent = conn.execute(
        "SELECT SUM(usd_cost) FROM costs WHERE provider='fiber'"
    ).fetchone()[0] or 0.0
    conn.close()
    return json.dumps({
        "ok": ok, "not_found": not_found, "errors": errors,
        "fiber_cumulative_usd": round(total_spent, 2),
        "run_estimated_usd": estimate_usd,
    })


@mcp.tool()
async def get_credit_balance() -> str:
    """Fetch your current Fiber credits balance + monthly cap."""
    if not FIBER_API_KEY:
        return json.dumps({"error": "FIBER_API_KEY not set"})
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {FIBER_API_KEY}"}
    async with streamablehttp_client(FIBER_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool("getOrgCredits_tool", {})
            payload = json.loads(r.content[0].text)
            out = payload.get("data", {}).get("output") or {}
            return json.dumps({
                "max": out.get("max"),
                "used": out.get("used"),
                "available": out.get("available"),
                "resets_on": out.get("usagePeriodResetsOn"),
                "estimated_companies_affordable": (
                    out.get("available", 0) // 4 if out.get("available") else 0
                ),
            })


# ---------- Person-level enrichment (Fiber Core syncQuickContactReveal) ----------

FIBER_CORE_URL = "https://mcp.fiber.ai/mcp"


def _parse_reveal_response(payload: dict[str, Any]) -> dict[str, Any]:
    """Map Fiber's contact-reveal response into our row shape."""
    if payload.get("status") != 200:
        return {
            "status": "error",
            "work_email": None, "personal_email": None, "all_emails": None,
            "phone_numbers": None,
            "credits": 0, "raw": json.dumps(payload),
        }
    data = payload.get("data") or {}
    profile = ((data.get("output") or {}).get("profile") or {})
    emails = profile.get("emails") or []
    phones = profile.get("phoneNumbers") or []
    work = next((e["email"] for e in emails if e.get("type") == "work"), None)
    personal = next((e["email"] for e in emails if e.get("type") == "personal"), None)
    all_emails = ",".join(e["email"] for e in emails if e.get("email"))
    all_phones = ",".join(p["number"] for p in phones if p.get("number"))
    state = profile.get("status") or "unknown"
    credits = (data.get("chargeInfo") or {}).get("creditsCharged") or 0
    return {
        "status": "ok" if (emails or phones) else "not_found",
        "work_email": work,
        "personal_email": personal,
        "all_emails": all_emails or None,
        "phone_numbers": all_phones or None,
        "credits": credits,
        "raw": json.dumps(payload),
    }


async def _reveal_one(
    session: Any,
    linkedin_url: str,
    *,
    get_work_emails: bool,
    get_personal_emails: bool,
    get_phone_numbers: bool,
    validate_emails: bool,
) -> dict[str, Any]:
    r = await session.call_tool("call_operation", {
        "operationId": "syncQuickContactReveal",
        "params": {
            "apiKey": FIBER_API_KEY,
            "linkedinUrl": linkedin_url,
            "enrichmentType": {
                "getWorkEmails": get_work_emails,
                "getPersonalEmails": get_personal_emails,
                "getPhoneNumbers": get_phone_numbers,
            },
            "validateEmails": validate_emails,
        },
    })
    return _parse_reveal_response(json.loads(r.content[0].text))


def _save_reveal(
    conn: sqlite3.Connection, linkedin_url: str, parsed: dict[str, Any]
) -> None:
    conn.execute(
        """
        INSERT INTO contact_reveal(
          linkedin_url, work_email, personal_email, all_emails,
          phone_numbers, status, revealed_at, raw_payload
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(linkedin_url) DO UPDATE SET
          work_email=excluded.work_email,
          personal_email=excluded.personal_email,
          all_emails=excluded.all_emails,
          phone_numbers=excluded.phone_numbers,
          status=excluded.status,
          revealed_at=excluded.revealed_at,
          raw_payload=excluded.raw_payload
        """,
        (
            linkedin_url, parsed["work_email"], parsed["personal_email"],
            parsed["all_emails"], parsed["phone_numbers"],
            parsed["status"], _now(), parsed["raw"],
        ),
    )


@mcp.tool()
async def reveal_contact(
    linkedin_url: str,
    get_work_emails: bool = True,
    get_personal_emails: bool = True,
    get_phone_numbers: bool = False,
    force: bool = False,
) -> str:
    """Reveal one person's emails (+ optional phone) via Fiber Core.

    Args:
        linkedin_url: The person's LinkedIn URL (must exist in your people table).
        get_work_emails: Include work email lookup (default True).
        get_personal_emails: Include personal email lookup (default True).
        get_phone_numbers: Include phone-number lookup (default False — usually
            not needed for outreach and adds ~1 credit per person).
        force: If True, re-runs even if cached. Default False = skip if
            we've already revealed this person.

    Cost: ~1 credit per requested enrichment type per person, ≈ $0.04-$0.06
    for the default work + personal emails.
    """
    if not FIBER_API_KEY:
        return json.dumps({"error": "FIBER_API_KEY not set"})
    if not any([get_work_emails, get_personal_emails, get_phone_numbers]):
        return json.dumps({"error": "at least one enrichment type must be true"})
    conn = _rw_conn()
    try:
        if not conn.execute(
            "SELECT 1 FROM people WHERE linkedin_url=?", (linkedin_url,)
        ).fetchone():
            return json.dumps({"error": "unknown linkedin_url", "linkedin_url": linkedin_url})
        if not force:
            cached = conn.execute(
                "SELECT work_email, personal_email, all_emails, phone_numbers, status, revealed_at "
                "FROM contact_reveal WHERE linkedin_url=?", (linkedin_url,)
            ).fetchone()
            if cached:
                return json.dumps({
                    "cached": True,
                    "linkedin_url": linkedin_url,
                    "work_email": cached["work_email"],
                    "personal_email": cached["personal_email"],
                    "all_emails": cached["all_emails"],
                    "phone_numbers": cached["phone_numbers"],
                    "status": cached["status"],
                    "revealed_at": cached["revealed_at"],
                })

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        headers = {"Authorization": f"Bearer {FIBER_API_KEY}"}
        async with streamablehttp_client(FIBER_CORE_URL, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                parsed = await _reveal_one(
                    session, linkedin_url,
                    get_work_emails=get_work_emails,
                    get_personal_emails=get_personal_emails,
                    get_phone_numbers=get_phone_numbers,
                    validate_emails=True,
                )

        _save_reveal(conn, linkedin_url, parsed)
        log_cost_via = _rw_conn  # avoid shadowing
        # log cost via the existing helper pattern (use module-level log)
        with conn:
            conn.execute(
                "INSERT INTO costs(ts, provider, operation, units, usd_cost, context)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (_now(), "fiber", "syncQuickContactReveal",
                 parsed["credits"], parsed["credits"] * USD_PER_CREDIT, linkedin_url),
            )
        return json.dumps({
            "cached": False,
            "linkedin_url": linkedin_url,
            "work_email": parsed["work_email"],
            "personal_email": parsed["personal_email"],
            "all_emails": parsed["all_emails"],
            "phone_numbers": parsed["phone_numbers"],
            "status": parsed["status"],
            "credits_charged": parsed["credits"],
            "usd_cost": round(parsed["credits"] * USD_PER_CREDIT, 4),
        })
    finally:
        conn.close()


@mcp.tool()
async def reveal_contacts_by_filter(
    filter_sql: str | None = None,
    max_people: int = 50,
    dry_run: bool = True,
    get_phone_numbers: bool = False,
    max_spend_usd: float | None = None,
) -> str:
    """Bulk-reveal contact emails for people matching ``filter_sql``.

    Args:
        filter_sql: WHERE clause body against people_enriched. Examples:
            "role_bucket='Marketing' AND seniority='VP' AND industry='Software Development'"
            "tags LIKE '%demo-scheduled%'"
            "connected_on >= date('now','-6 months') AND role_bucket='Founder'"
            Pass None to target all un-revealed people (almost never what you want).
        max_people: Hard cap on people processed in this call (default 50, max 500).
        dry_run: True (default) returns scope + cost estimate without spending.
            Set False to actually reveal.
        get_phone_numbers: Include phone lookup (default False — extra cost).
        max_spend_usd: Hard ceiling that aborts mid-run if exceeded.

    Cost ≈ $0.04-$0.06 per person (work + personal email). Phone adds ~$0.02.
    People already revealed are skipped (idempotent).
    """
    if not FIBER_API_KEY:
        return json.dumps({"error": "FIBER_API_KEY not set"})
    _safe = filter_sql or "1=1"
    if _DANGER_RE.search(_safe):
        return json.dumps({"error": "filter contains write/DDL keywords"})
    max_people = max(1, min(max_people, 500))

    enrichment_types_requested = 2 + (1 if get_phone_numbers else 0)
    per_person_credits = enrichment_types_requested
    per_person_usd = per_person_credits * USD_PER_CREDIT

    target_sql = f"""
        SELECT pe.linkedin_url, pe.first_name, pe.last_name,
               pe.company_name, pe.role_bucket, pe.seniority
        FROM people_enriched pe
        LEFT JOIN contact_reveal cr ON cr.linkedin_url = pe.linkedin_url
        WHERE cr.linkedin_url IS NULL
          AND ({_safe})
        LIMIT {max_people}
    """
    conn = _rw_conn()
    targets = conn.execute(target_sql).fetchall()
    est_usd = round(len(targets) * per_person_usd, 2)

    if dry_run or not targets:
        conn.close()
        return json.dumps({
            "dry_run": dry_run,
            "would_reveal": len(targets),
            "credits_per_person": per_person_credits,
            "estimated_usd": est_usd,
            "first_5": [
                {"name": f"{t['first_name']} {t['last_name']}",
                 "company": t["company_name"], "role": t["role_bucket"],
                 "seniority": t["seniority"]}
                for t in targets[:5]
            ],
            "hint": "set dry_run=false to actually run; consider max_spend_usd as a safety net",
        })

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    headers = {"Authorization": f"Bearer {FIBER_API_KEY}"}
    ok = not_found = errors = 0
    spent = 0.0
    aborted_at = None

    async with streamablehttp_client(FIBER_CORE_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for i, t in enumerate(targets, start=1):
                if max_spend_usd is not None and spent + per_person_usd > max_spend_usd:
                    aborted_at = i - 1
                    break
                try:
                    parsed = await _reveal_one(
                        session, t["linkedin_url"],
                        get_work_emails=True,
                        get_personal_emails=True,
                        get_phone_numbers=get_phone_numbers,
                        validate_emails=True,
                    )
                except Exception:
                    errors += 1
                    continue
                _save_reveal(conn, t["linkedin_url"], parsed)
                credits = parsed["credits"]
                spend = credits * USD_PER_CREDIT
                with conn:
                    conn.execute(
                        "INSERT INTO costs(ts, provider, operation, units, usd_cost, context)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (_now(), "fiber", "syncQuickContactReveal",
                         credits, spend, t["linkedin_url"]),
                    )
                spent += spend
                if parsed["status"] == "ok":
                    ok += 1
                elif parsed["status"] == "not_found":
                    not_found += 1
                else:
                    errors += 1
    conn.close()
    return json.dumps({
        "ok": ok, "not_found": not_found, "errors": errors,
        "spent_usd": round(spent, 4),
        "aborted_at_index": aborted_at,
    })


# ---------- Pipeline (ingest / dedupe / classify) ----------

@mcp.tool()
def pipeline_status() -> str:
    """Snapshot of what's in the DB and what's pending.

    Call this FIRST on a fresh DB or when orienting. Returns:
      - row counts per stage
      - how many companies are un-enriched
      - how many people are un-classified
      - whether the Anthropic key + Fiber key are configured
      - cumulative cost so far
    """
    from netcrm import db
    conn = _rw_conn()
    db.apply_migrations(conn, MIGRATIONS_DIR)
    n_people = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    n_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    n_classified = conn.execute("SELECT COUNT(*) FROM people_class").fetchone()[0]
    n_enriched = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE fiber_status='ok'"
    ).fetchone()[0]
    n_unenriched = conn.execute(
        "SELECT COUNT(*) FROM companies "
        "WHERE fiber_enriched_at IS NULL "
        "  AND (fiber_status IS NULL OR fiber_status='error')"
    ).fetchone()[0]
    costs = {}
    for r in conn.execute(
        "SELECT provider, SUM(usd_cost) AS usd FROM costs GROUP BY provider"
    ).fetchall():
        costs[r["provider"]] = round(r["usd"] or 0.0, 4)
    conn.close()
    return json.dumps({
        "db_path": str(DB_PATH),
        "people":          n_people,
        "people_classified": n_classified,
        "people_pending_classification": max(0, n_people - n_classified),
        "companies":       n_companies,
        "companies_enriched": n_enriched,
        "companies_pending_enrichment": n_unenriched,
        "cost_so_far_usd": costs,
        "config": {
            "anthropic_key_set": bool(ANTHROPIC_API_KEY),
            "fiber_key_set":     bool(FIBER_API_KEY),
            "model":             ANTHROPIC_MODEL,
        },
    }, indent=2)


@mcp.tool()
def find_linkedin_csv() -> str:
    """Scan common locations for a LinkedIn Connections.csv export.

    Searches ~/Downloads, ~/Desktop, and ~/Documents for files named
    "Connections.csv" (the file LinkedIn ships inside its export zip).
    Also flags any LinkedIn export zip files the user hasn't unzipped yet.

    Use this when the user says "I have my CSV, can you find it" rather
    than ask them for the exact path.
    """
    import glob
    home = Path.home()
    candidates = []
    zips = []
    for root in (home / "Downloads", home / "Desktop", home / "Documents"):
        if not root.exists():
            continue
        # Direct Connections.csv hits
        for hit in glob.glob(str(root / "**" / "Connections.csv"), recursive=True):
            try:
                size = Path(hit).stat().st_size
                mtime = Path(hit).stat().st_mtime
                candidates.append({"path": hit, "size_bytes": size, "mtime": mtime})
            except OSError:
                pass
        # Unzipped LinkedIn export zips the user may have missed
        for hit in glob.glob(str(root / "Basic_LinkedInDataExport*.zip")):
            zips.append(hit)
        for hit in glob.glob(str(root / "*LinkedIn*Export*.zip")):
            if hit not in zips:
                zips.append(hit)

    # Sort candidates by mtime (most recent first)
    candidates.sort(key=lambda c: c["mtime"], reverse=True)
    return json.dumps({
        "found": candidates,
        "unzipped_archives": zips,
        "hint": (
            "If 'found' is empty but 'unzipped_archives' has entries, the user "
            "needs to unzip the LinkedIn export first. The CSV is inside the zip."
        ) if not candidates and zips else None,
    }, indent=2, default=str)


@mcp.tool()
def ingest_csv(csv_path: str) -> str:
    """Load a LinkedIn ``Connections.csv`` export into the people table.

    Args:
        csv_path: Path to the file. May be absolute, '~'-prefixed, or
            relative to the cwd of the MCP server. The LinkedIn export's
            'Notes:' preamble is auto-skipped.

    Free (no API cost). Idempotent: re-running on the same CSV is a no-op.
    """
    from netcrm import db, ingest
    path = Path(csv_path).expanduser().resolve()
    if not path.exists():
        return json.dumps({"error": f"file not found: {path}"})
    conn = _rw_conn()
    db.apply_migrations(conn, MIGRATIONS_DIR)
    try:
        n = ingest.ingest_csv(conn, path)
    except ValueError as e:
        return json.dumps({"error": str(e), "hint":
            "Headers must match LinkedIn export: First Name, Last Name, URL, "
            "Email Address, Company, Position, Connected On"})
    finally:
        conn.close()
    return json.dumps({
        "ok": True,
        "rows_loaded": n,
        "csv_path": str(path),
        "next_step": "Call dedupe_companies(), then classify_people(dry_run=true) for a cost estimate.",
    })


@mcp.tool()
def dedupe_companies() -> str:
    """Populate the companies table from distinct people.company_key values.

    Free (no API cost). Idempotent. Run after ingest_csv.
    """
    from netcrm import companies
    conn = _rw_conn()
    try:
        n = companies.dedupe_companies(conn)
    finally:
        conn.close()
    return json.dumps({
        "ok": True,
        "companies": n,
        "next_step": "Call classify_people(dry_run=true) for a cost estimate.",
    })


@mcp.tool()
def classify_people(
    max_spend_usd: float | None = None,
    batch_size: int = 50,
    dry_run: bool = True,
) -> str:
    """Classify every un-classified person via Anthropic Haiku.

    Args:
        max_spend_usd: Hard cap on this run's spend. Aborts mid-run if hit.
            None = no cap. Recommended: $5 for ~5000 people.
        batch_size: People per LLM call (max 100). Default 50.
        dry_run: When True (default), prints estimated cost and exits. Set
            False to actually call Anthropic.

    The dry-run estimate uses conservative token counts and often
    UNDER-estimates by ~5×. Real cost for ~5,000 people is typically $1–2.
    """
    if not dry_run and not ANTHROPIC_API_KEY:
        return json.dumps({"error": "ANTHROPIC_API_KEY not set"})
    if batch_size > 100:
        return json.dumps({"error": "batch_size must be <= 100"})
    from netcrm import classify, cost as cost_mod
    conn = _rw_conn()
    unclassified = classify.count_unclassified(conn)
    usd_in  = float(os.environ.get("HAIKU_USD_PER_INPUT_MTOK",  "1.00"))
    usd_out = float(os.environ.get("HAIKU_USD_PER_OUTPUT_MTOK", "5.00"))
    est = cost_mod.estimate_haiku(
        unclassified, batch_size=batch_size,
        input_tokens_per_call=600, output_tokens_per_call=200,
        usd_per_input_mtok=usd_in, usd_per_output_mtok=usd_out,
    )
    if dry_run:
        conn.close()
        return json.dumps({
            "dry_run": True,
            "would_classify": unclassified,
            "estimated_usd": round(est, 4),
            "note": "Real cost typically ~5x this estimate; budget accordingly.",
        })
    if unclassified == 0:
        conn.close()
        return json.dumps({"ok": True, "would_classify": 0, "note": "everyone is already classified"})

    import anthropic
    from netcrm.anthropic_client import ClassifierClient
    sdk = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    classifier = ClassifierClient(sdk, model=ANTHROPIC_MODEL)
    tracker = cost_mod.CostTracker(conn, max_spend_usd=max_spend_usd)
    try:
        n_batches = classify.classify_people(
            conn, classifier, tracker,
            model=ANTHROPIC_MODEL, batch_size=batch_size,
            usd_per_input_mtok=usd_in, usd_per_output_mtok=usd_out,
        )
    except cost_mod.SpendCapExceeded as e:
        conn.close()
        return json.dumps({"aborted": True, "reason": str(e),
                           "spent_usd": round(tracker.spent_usd, 4)})
    finally:
        if conn:
            conn.close()
    return json.dumps({
        "ok": True,
        "batches": n_batches,
        "spent_usd": round(tracker.spent_usd, 4),
    })


@mcp.tool()
def build_views() -> str:
    """(Re-)create the people_enriched view. Run after schema changes."""
    from netcrm import views
    conn = _rw_conn()
    try:
        views.build_views(conn)
    finally:
        conn.close()
    return json.dumps({"ok": True})


def main() -> None:
    """Entrypoint for `python -m netcrm.mcp_server`."""
    mcp.run()


if __name__ == "__main__":
    main()
