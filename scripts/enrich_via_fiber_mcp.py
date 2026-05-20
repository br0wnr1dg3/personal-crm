"""One-off Fiber V2 MCP enrichment, scoped to a filtered slice of companies.

Approach (because companySearch filters silently no-op on Fiber V2):
  1. For each target company, pick the senior-most GTM contact at that company.
  2. Call profileLiveEnrich on their LinkedIn URL (2 credits) → extract the
     LinkedIn company slug from detailed_work_experiences matching by name.
  3. Call companyLiveEnrich(type="slug", value=<slug>) (2 credits) → write
     industry / employee_band / funding / hq / website / description into the
     companies table.

Cost logging goes into the same `costs` table netcrm uses, so cost-report
shows the total at the end.

Configurable via the FILTER_SQL constant below. Resume-safe: skips companies
already enriched (fiber_enriched_at IS NOT NULL).
"""
from __future__ import annotations
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# --- config ---

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=REPO_ROOT / ".env")

DB_PATH = REPO_ROOT / "crm.db"
URL = "https://mcp.fiber.ai/mcp/v2"
HEADERS = {"Authorization": f"Bearer {os.environ['FIBER_API_KEY']}"}
USD_PER_CREDIT = float(os.environ.get("FIBER_USD_PER_CREDIT", "0.020"))

# Pick targets: senior-most GTM contact per recently-connected company.
# Score seniority: Founder=1, C-suite=2, VP=3, Director=4 (lower = better)
FILTER_SQL = """
WITH ranked AS (
  SELECT
    c.company_key,
    c.display_name,
    p.linkedin_url,
    p.first_name,
    p.last_name,
    pc.role_bucket,
    pc.seniority,
    ROW_NUMBER() OVER (
      PARTITION BY c.company_key
      ORDER BY
        CASE pc.seniority
          WHEN 'Founder'  THEN 1
          WHEN 'C-suite'  THEN 2
          WHEN 'VP'       THEN 3
          WHEN 'Director' THEN 4
          ELSE 5
        END,
        p.connected_on DESC
    ) AS rk
  FROM companies c
  JOIN people p ON p.company_key = c.company_key
  JOIN people_class pc ON pc.linkedin_url = p.linkedin_url
  WHERE p.connected_on >= date('now', '-24 months')
    AND pc.role_bucket IN ('Sales', 'BD', 'Marketing', 'Founder')
    AND pc.seniority IN ('Director', 'VP', 'C-suite', 'Founder')
)
SELECT company_key, display_name, linkedin_url, first_name, last_name, role_bucket, seniority
FROM ranked
WHERE rk = 1
ORDER BY company_key
"""

DUMP_FIRST_N = 1  # number of full response dumps for sanity check


# --- helpers ---

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_for_match(s: str) -> str:
    """Loose match for company names across CSV/profile data."""
    if not s:
        return ""
    s = s.lower().strip()
    for suf in [", inc.", ", inc", " inc.", " inc", ", llc", " llc",
                ", ltd.", " ltd.", " ltd", " gmbh", ", gmbh", " plc",
                " corp.", " corp", " corporation", " co.", " co"]:
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.strip().replace("&", "and").replace(".", "").strip()


def log_cost(conn: sqlite3.Connection, provider: str, op: str,
             units: int, usd_cost: float, context: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO costs(ts, provider, operation, units, usd_cost, context)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (now_iso(), provider, op, units, usd_cost, context),
        )


def extract_slug_for_company(
    profile_payload: dict[str, Any], company_display: str, company_key: str
) -> tuple[str | None, str]:
    """Return (slug, match_reason). slug=None if no match."""
    try:
        dwe = (
            profile_payload["data"]["output"]["profile"].get("detailed_work_experiences")
            or []
        )
    except (KeyError, TypeError):
        return None, "no_detailed_work_experiences"

    target_loose = normalize_for_match(company_display)
    target_key = company_key

    # Pass 1: case-insensitive exact match on raw company_name
    for exp in dwe:
        cname = (exp.get("company_name") or "").strip()
        if cname.lower() == company_display.lower():
            cd = exp.get("company_details") or {}
            slug = cd.get("linkedin_primary_slug")
            if slug:
                return slug, "exact"

    # Pass 2: loose normalization match
    for exp in dwe:
        cname = exp.get("company_name") or ""
        if normalize_for_match(cname) == target_loose:
            cd = exp.get("company_details") or {}
            slug = cd.get("linkedin_primary_slug")
            if slug:
                return slug, "loose"

    # Pass 3: substring match (target normalized appears in experience normalized)
    for exp in dwe:
        cname = exp.get("company_name") or ""
        nexp = normalize_for_match(cname)
        if target_loose and (target_loose in nexp or nexp in target_loose):
            cd = exp.get("company_details") or {}
            slug = cd.get("linkedin_primary_slug")
            if slug:
                return slug, "substring"

    return None, "no_match"


def map_company_fields(company: dict[str, Any]) -> dict[str, Any]:
    """Map Fiber companyLiveEnrich output → our companies columns.

    Field discovery is dump-driven: the first DUMP_FIRST_N calls print the
    full company object so we can iterate. This mapper handles the most
    common field names.
    """
    industries = company.get("industries") or []
    industry = industries[0]["name"] if industries and isinstance(industries[0], dict) else None
    sub_industry = (
        industries[1]["name"]
        if len(industries) > 1 and isinstance(industries[1], dict)
        else None
    )

    employee_count = company.get("employee_count")
    employee_band = None
    if isinstance(employee_count, int) and employee_count > 0:
        # Banding consistent with the schema's expected values
        if employee_count <= 10:        employee_band = "1-10"
        elif employee_count <= 50:      employee_band = "11-50"
        elif employee_count <= 200:     employee_band = "51-200"
        elif employee_count <= 500:     employee_band = "201-500"
        elif employee_count <= 1000:    employee_band = "501-1000"
        elif employee_count <= 5000:    employee_band = "1001-5000"
        elif employee_count <= 10000:   employee_band = "5001-10000"
        else:                            employee_band = "10001+"

    # HQ — Fiber V2 returns `inferred_location` and `locations[]`
    hq_country = hq_region = None
    inferred = company.get("inferred_location") or {}
    if isinstance(inferred, dict):
        hq_country = inferred.get("country_code") or inferred.get("country_name")
        hq_region = inferred.get("city") or inferred.get("state_name")
    if not hq_country:
        locs = company.get("locations") or []
        if isinstance(locs, list) and locs:
            primary = next((l for l in locs if l.get("is_primary")), locs[0]) if isinstance(locs[0], dict) else None
            if isinstance(primary, dict):
                loc = primary.get("location") or {}
                hq_country = loc.get("country_code") or loc.get("country_name")
                hq_region = loc.get("city") or loc.get("state_name")

    # Revenue band — derive from revenue_usd_lower_bound/upper_bound
    revenue_band = None
    lo = company.get("revenue_usd_lower_bound")
    hi = company.get("revenue_usd_upper_bound")
    rep = hi if isinstance(hi, (int, float)) else lo if isinstance(lo, (int, float)) else None
    if isinstance(rep, (int, float)) and rep > 0:
        if rep < 1_000_000:           revenue_band = "<$1M"
        elif rep < 10_000_000:        revenue_band = "$1M-$10M"
        elif rep < 50_000_000:        revenue_band = "$10M-$50M"
        elif rep < 100_000_000:       revenue_band = "$50M-$100M"
        elif rep < 500_000_000:       revenue_band = "$100M-$500M"
        elif rep < 1_000_000_000:     revenue_band = "$500M-$1B"
        else:                          revenue_band = "$1B+"

    # Funding stage — V2 companyLiveEnrich doesn't reliably expose this.
    # Leave NULL unless a field shows up in future probes.
    funding_stage = None

    # Website
    website = company.get("website") or company.get("domain")
    if not website:
        domains = company.get("domains")
        if isinstance(domains, list) and domains:
            d = domains[0]
            website = d if isinstance(d, str) else (d.get("domain") if isinstance(d, dict) else None)

    return {
        "industry": industry,
        "sub_industry": sub_industry,
        "employee_band": employee_band,
        "revenue_band": revenue_band,
        "funding_stage": funding_stage,
        "hq_country": hq_country,
        "hq_region": hq_region,
        "website": website,
        "description": company.get("description") or company.get("headline"),
    }


# --- main ---

async def main(dry_run: bool = False, limit: int | None = None) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    targets = list(conn.execute(FILTER_SQL).fetchall())
    # Skip already-enriched
    targets = [
        t for t in targets
        if not conn.execute(
            "SELECT fiber_enriched_at FROM companies WHERE company_key=?",
            (t["company_key"],),
        ).fetchone()["fiber_enriched_at"]
    ]
    if limit:
        targets = targets[:limit]
    print(f"to enrich: {len(targets)} companies")
    print(f"estimated cost (4 credits × ${USD_PER_CREDIT}/credit each): "
          f"${len(targets) * 4 * USD_PER_CREDIT:.2f}")
    if dry_run:
        print("(dry-run; exiting without API calls)")
        return

    async with streamablehttp_client(URL, headers=HEADERS) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            ok, not_found, errors = 0, 0, 0
            for idx, t in enumerate(targets, start=1):
                ck = t["company_key"]
                disp = t["display_name"]
                purl = t["linkedin_url"]
                rep = f"{t['first_name']} {t['last_name']} ({t['role_bucket']}/{t['seniority']})"
                print(f"[{idx:>3}/{len(targets)}] {disp!r} ← {rep}")

                # Step 1: profileLiveEnrich
                try:
                    pr = await session.call_tool("profileLiveEnrich_tool", {
                        "identifier": purl,
                        "getDetailedWorkExperience": True,
                    })
                    pp = json.loads(pr.content[0].text)
                except Exception as e:
                    print(f"  profile error: {type(e).__name__}: {e}")
                    errors += 1
                    with conn:
                        conn.execute(
                            "UPDATE companies SET fiber_status='error' WHERE company_key=?",
                            (ck,),
                        )
                    continue

                if pp.get("status") != 200:
                    print(f"  profile status={pp.get('status')}")
                    log_cost(conn, "fiber", "profile_live_enrich", 0, 0.0, ck)
                    with conn:
                        conn.execute(
                            "UPDATE companies SET fiber_status='error' WHERE company_key=?",
                            (ck,),
                        )
                    errors += 1
                    continue

                p_credits = (pp.get("data", {}).get("chargeInfo") or {}).get("creditsCharged") or 0
                log_cost(conn, "fiber", "profile_live_enrich",
                         p_credits, p_credits * USD_PER_CREDIT, ck)

                slug, reason = extract_slug_for_company(pp, disp, ck)
                if not slug:
                    print(f"  no slug match ({reason})")
                    not_found += 1
                    with conn:
                        conn.execute(
                            "UPDATE companies SET fiber_status='not_found', fiber_enriched_at=? "
                            "WHERE company_key=?",
                            (now_iso(), ck),
                        )
                    continue

                # Step 2: companyLiveEnrich
                try:
                    cr = await session.call_tool("companyLiveEnrich_tool", {
                        "type": "slug",
                        "value": slug,
                    })
                    cp = json.loads(cr.content[0].text)
                except Exception as e:
                    print(f"  company error: {type(e).__name__}: {e}")
                    errors += 1
                    with conn:
                        conn.execute(
                            "UPDATE companies SET fiber_status='error' WHERE company_key=?",
                            (ck,),
                        )
                    continue

                if cp.get("status") != 200:
                    print(f"  company status={cp.get('status')} slug={slug!r}")
                    log_cost(conn, "fiber", "company_live_enrich", 0, 0.0, ck)
                    with conn:
                        conn.execute(
                            "UPDATE companies SET fiber_status='error' WHERE company_key=?",
                            (ck,),
                        )
                    errors += 1
                    continue

                c_credits = (cp.get("data", {}).get("chargeInfo") or {}).get("creditsCharged") or 0
                log_cost(conn, "fiber", "company_live_enrich",
                         c_credits, c_credits * USD_PER_CREDIT, ck)

                company = (cp.get("data", {}).get("output") or {}).get("company") or {}

                if idx <= DUMP_FIRST_N:
                    print(f"  --- DUMP company fields for sanity ---")
                    print(f"  company top keys: {sorted(company.keys())[:30]}")
                    sample = {k: company[k] for k in list(company.keys())[:20] if k in company}
                    print("  " + json.dumps(sample, indent=2)[:1500].replace("\n", "\n  "))
                    print(f"  --- end dump ---")

                fields = map_company_fields(company)
                fields["fiber_enriched_at"] = now_iso()
                fields["fiber_status"] = "ok"
                fields["company_key"] = ck

                with conn:
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
                print(f"  ok ← industry={fields['industry']!r} "
                      f"employees={fields['employee_band']!r} "
                      f"funding={fields['funding_stage']!r}")
                ok += 1

            print()
            print("=== Summary ===")
            print(f"  ok:         {ok}")
            print(f"  not_found:  {not_found}")
            print(f"  errors:     {errors}")
            total = conn.execute(
                "SELECT SUM(usd_cost) FROM costs WHERE provider='fiber'"
            ).fetchone()[0] or 0.0
            print(f"  fiber spent (cumulative, all-time): ${total:.2f}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    lim = None
    for a in sys.argv:
        if a.startswith("--limit="):
            lim = int(a.split("=", 1)[1])
    asyncio.run(main(dry_run=dry, limit=lim))
