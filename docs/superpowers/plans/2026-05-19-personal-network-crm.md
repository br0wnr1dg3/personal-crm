# Personal Network CRM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Python pipeline that turns a LinkedIn `connections.csv` export into an enriched, queryable SQLite database, served via Datasette.

**Architecture:** Staged + cached CLI tool (`netcrm`). Stages: `ingest → dedupe-companies → enrich-companies → classify-people → build-views`. Each stage is idempotent and writes to its own table or columns. Company-level enrichment via direct Fiber AI HTTP calls; role/seniority classification via Anthropic Haiku with tool-use JSON output.

**Tech Stack:** Python 3.11+, SQLite (stdlib), Typer (CLI), httpx (HTTP), anthropic (LLM), python-dotenv (env), pytest + respx (testing), Datasette (UI, used at runtime not in code).

**Spec:** `docs/superpowers/specs/2026-05-19-personal-network-crm-design.md`

## File map

```
netcrm/
  __init__.py
  db.py                 # SQLite connect + migration runner
  normalize.py          # pure: company_key(raw) → normalized string
  ingest.py             # parse_row, ingest_csv → people table
  companies.py          # dedupe_companies, enrich_companies → companies table
  classify.py           # build_prompt, parse_response (pure) + classify_people (wiring)
  views.py              # build_views: CREATE VIEW IF NOT EXISTS
  fiber.py              # FiberClient: HTTP wrapper for org enrichment
  anthropic_client.py   # ClassifierClient: batched Haiku tool-use
  cost.py               # CostTracker: estimator, log writer, spend cap
  _stubs.py             # StubFiberClient, StubClassifierClient (test mode + smoke tests)
  cli.py                # Typer entrypoint; sub-commands map 1:1 to stages
migrations/
  001_init.sql          # all tables + indexes
saved_queries/
  reconnect_targets.sql
  sales_at_growth_companies.sql
  marketing_leaders.sql
  founders_in_target_industries.sql
metadata.yml            # Datasette config + canned-query references
tests/
  conftest.py           # fixtures: tmp_db, fiber_stub, anthropic_stub
  fixtures/
    tiny_connections.csv
    fiber_canned_responses.json
  test_normalize.py
  test_db.py
  test_ingest.py
  test_companies.py
  test_cost.py
  test_fiber.py
  test_classify.py
  test_cli_smoke.py     # end-to-end smoke
pyproject.toml
.env.example
.gitignore
README.md
```

---

## Task 1: Project skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `netcrm/__init__.py`
- Create: `migrations/.gitkeep`
- Create: `saved_queries/.gitkeep`
- Create: `tests/__init__.py`
- Create: `tests/fixtures/.gitkeep`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "netcrm"
version = "0.1.0"
description = "Personal network CRM built from LinkedIn connections.csv"
requires-python = ">=3.11"
dependencies = [
  "typer>=0.12",
  "httpx>=0.27",
  "anthropic>=0.40",
  "python-dotenv>=1.0",
  "tqdm>=4.66",
]

[project.optional-dependencies]
dev = [
  "pytest>=8",
  "respx>=0.21",
  "pytest-asyncio>=0.23",
]
serve = [
  "datasette>=1.0a13",
]

[project.scripts]
netcrm = "netcrm.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v"
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.py[cod]
.venv/
.env
crm.db
crm.db-journal
.pytest_cache/
*.egg-info/
dist/
build/
```

- [ ] **Step 3: Create `.env.example`**

```
# Fiber AI organization enrichment
FIBER_API_KEY=
FIBER_API_BASE_URL=https://api.fiberai.com
FIBER_USD_PER_CREDIT=0.020

# Anthropic for Haiku classification
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-haiku-4-5-20251001

# SQLite database path (default: ./crm.db in cwd)
NETCRM_DB_PATH=crm.db
```

- [ ] **Step 4: Create empty package files**

```bash
mkdir -p netcrm migrations saved_queries tests/fixtures
touch netcrm/__init__.py tests/__init__.py
touch migrations/.gitkeep saved_queries/.gitkeep tests/fixtures/.gitkeep
```

- [ ] **Step 5: Verify install works**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
netcrm --help
```

Expected: typer prints "Usage: netcrm [OPTIONS]" or "no commands defined yet" — either fine. `pip install` must succeed without errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore .env.example netcrm/ migrations/ saved_queries/ tests/
git commit -m "feat: project skeleton (pyproject, package layout, env example)"
```

---

## Task 2: SQLite connection helper + migration runner

**Files:**
- Create: `netcrm/db.py`
- Create: `tests/test_db.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write the failing test**

`tests/conftest.py`:
```python
from pathlib import Path
import pytest

@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"
```

`tests/test_db.py`:
```python
from pathlib import Path
from netcrm import db

def test_connect_creates_file(tmp_db_path: Path):
    conn = db.connect(tmp_db_path)
    conn.close()
    assert tmp_db_path.exists()

def test_apply_migrations_runs_each_once(tmp_db_path: Path, tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_init.sql").write_text("CREATE TABLE x(a INTEGER);")
    (migrations_dir / "002_more.sql").write_text("CREATE TABLE y(b INTEGER);")

    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, migrations_dir)
    # second call must be a no-op
    db.apply_migrations(conn, migrations_dir)

    rows = conn.execute(
        "SELECT filename FROM _migrations ORDER BY filename"
    ).fetchall()
    assert [r[0] for r in rows] == ["001_init.sql", "002_more.sql"]
    # tables actually got created
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "x" in tables and "y" in tables
    conn.close()

def test_apply_migrations_runs_in_filename_order(tmp_db_path: Path, tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "002_b.sql").write_text("CREATE TABLE later(x INTEGER);")
    (migrations_dir / "001_a.sql").write_text("CREATE TABLE earlier(x INTEGER);")
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, migrations_dir)
    rows = conn.execute(
        "SELECT filename FROM _migrations ORDER BY applied_at"
    ).fetchall()
    assert [r[0] for r in rows] == ["001_a.sql", "002_b.sql"]
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'netcrm.db'`.

- [ ] **Step 3: Write minimal implementation**

`netcrm/db.py`:
```python
"""SQLite connection + migration runner."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS _migrations (
  filename   TEXT PRIMARY KEY,
  applied_at TIMESTAMP NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def apply_migrations(conn: sqlite3.Connection, migrations_dir: str | Path) -> None:
    conn.execute(_MIGRATIONS_TABLE)
    applied = {
        r["filename"]
        for r in conn.execute("SELECT filename FROM _migrations").fetchall()
    }
    for sql_file in sorted(Path(migrations_dir).glob("*.sql")):
        if sql_file.name in applied:
            continue
        with conn:
            conn.executescript(sql_file.read_text())
            conn.execute(
                "INSERT INTO _migrations(filename, applied_at) VALUES (?, ?)",
                (sql_file.name, datetime.now(timezone.utc).isoformat()),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add netcrm/db.py tests/test_db.py tests/conftest.py
git commit -m "feat(db): sqlite connect + idempotent migration runner"
```

---

## Task 3: Initial schema migration

**Files:**
- Create: `migrations/001_init.sql`
- Modify: `tests/test_db.py` (add schema-load test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:
```python
def test_001_init_creates_all_tables(tmp_db_path: Path):
    from netcrm import db
    repo_root = Path(__file__).parent.parent
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, repo_root / "migrations")
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    expected = {"people", "companies", "people_class", "costs", "_migrations"}
    assert expected.issubset(tables)
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py::test_001_init_creates_all_tables -v`
Expected: FAIL — migrations directory has no `.sql` files yet, so no tables are created.

- [ ] **Step 3: Write the schema**

`migrations/001_init.sql`:
```sql
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py::test_001_init_creates_all_tables -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add migrations/001_init.sql tests/test_db.py
git commit -m "feat(db): initial schema (people, companies, people_class, costs)"
```

---

## Task 4: Test fixture — tiny_connections.csv + canned Fiber responses

**Files:**
- Create: `tests/fixtures/tiny_connections.csv`
- Create: `tests/fixtures/fiber_canned_responses.json`

- [ ] **Step 1: Write the CSV fixture** (covers edge cases)

`tests/fixtures/tiny_connections.csv`:
```csv
Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing..."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Alice,Smith,https://www.linkedin.com/in/alice-smith-1,alice@example.com,Acme Inc.,VP of Sales,15 May 2026
Bob,Jones,https://www.linkedin.com/in/bob-jones-2,,"ACME, Inc",Senior Software Engineer,10 Mar 2024
Carol,O'Brien,https://www.linkedin.com/in/carol-obrien-3,,Initech Ltd.,Founder & CEO,01 Jan 2022
Daniel,Müller,https://www.linkedin.com/in/daniel-muller-4,,Globex GmbH,Marketing Director,30 Jun 2025
Eve,Tan,https://www.linkedin.com/in/eve-tan-5,eve@umbrella.co,Umbrella Corp,Head of Growth,12 Feb 2026
Frank,Brown,https://www.linkedin.com/in/frank-brown-6,,,Consultant,05 Aug 2023
Grace,Hopper,https://www.linkedin.com/in/grace-hopper-7,,Soylent,Founding Engineer,17 Sep 2025
Hank,Lee,https://www.linkedin.com/in/hank-lee-8,,Tesla,Marketing Lead,22 Nov 2025
Ivy,Park,https://www.linkedin.com/in/ivy-park-9,,Massive Dynamic,Business Development Manager,03 Apr 2026
Jack,Doe,https://www.linkedin.com/in/jack-doe-10,,Wayne Enterprises,Chief Product Officer,18 Dec 2023
Kara,Zor-El,https://www.linkedin.com/in/kara-zor-el-11,,Stark Industries,Student Intern,02 Jul 2024
Liam,Foley,https://www.linkedin.com/in/liam-foley-12,,Stark Industries,VP of Engineering,11 Oct 2024
Mia,Chen,https://www.linkedin.com/in/mia-chen-13,,Acme Inc,Account Executive,28 Feb 2025
Noah,Khan,https://www.linkedin.com/in/noah-khan-14,,Sequoia Capital,Principal,19 Jun 2026
Olivia,Reed,https://www.linkedin.com/in/olivia-reed-15,,,,29 Sep 2025
Pete,Singh,https://www.linkedin.com/in/pete-singh-16,,Nuts & Bolts AI,Co-founder,01 Jan 2024
Quinn,Walsh,https://www.linkedin.com/in/quinn-walsh-17,,Cogent Labs Ltd.,Director of Marketing,14 Mar 2026
Rita,Mendes,https://www.linkedin.com/in/rita-mendes-18,,Globex GmbH,Software Engineer,20 May 2024
Sam,Park,https://www.linkedin.com/in/sam-park-19,,Acme Inc.,Customer Success Manager,07 Aug 2025
Tara,Singh,https://www.linkedin.com/in/tara-singh-20,,Nuts & Bolts AI,Head of Operations,21 Jan 2026
```

Coverage: legal-suffix variants ("Acme Inc." / "ACME, Inc" / "Acme Inc"), unicode names (Müller), apostrophes (O'Brien), missing email, missing company, missing position, multi-titled ("Founder & CEO"), "Founding [X]", "Head of X", same company different seniority (Stark), ampersand company name ("Nuts & Bolts AI").

- [ ] **Step 2: Write canned Fiber responses**

`tests/fixtures/fiber_canned_responses.json`:
```json
{
  "acme": {
    "industry": "Manufacturing",
    "sub_industry": "Industrial Equipment",
    "employee_band": "201-500",
    "revenue_band": "$50M-$100M",
    "funding_stage": "Public",
    "hq_country": "US",
    "hq_region": "California",
    "website": "https://acme.example.com",
    "description": "Industrial supplies."
  },
  "initech": {
    "industry": "Software",
    "sub_industry": "Enterprise SaaS",
    "employee_band": "51-200",
    "revenue_band": "$10M-$50M",
    "funding_stage": "Series B",
    "hq_country": "US",
    "hq_region": "Texas",
    "website": "https://initech.example.com",
    "description": "TPS reports automation."
  },
  "globex": {
    "industry": "Technology",
    "sub_industry": "Consumer Electronics",
    "employee_band": "1001-5000",
    "revenue_band": "$500M-$1B",
    "funding_stage": "Public",
    "hq_country": "DE",
    "hq_region": "Bavaria",
    "website": "https://globex.example.com",
    "description": "Consumer hardware giant."
  },
  "stark industries": {
    "industry": "Aerospace",
    "sub_industry": "Defense",
    "employee_band": "5001-10000",
    "revenue_band": "$1B+",
    "funding_stage": "Public",
    "hq_country": "US",
    "hq_region": "New York",
    "website": "https://stark.example.com",
    "description": "Advanced defense systems."
  },
  "nuts and bolts ai": {
    "industry": "Software",
    "sub_industry": "AI Tooling",
    "employee_band": "1-10",
    "revenue_band": "<$1M",
    "funding_stage": "Bootstrapped",
    "hq_country": "IE",
    "hq_region": "Dublin",
    "website": "https://nutsandbolts.ai",
    "description": "Personal automation."
  }
}
```

Note: keys here match the normalized `company_key` produced by the normalizer in Task 5 (lowercased, suffixes removed, `&` → `and`). Companies not in this file simulate "not_found".

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/tiny_connections.csv tests/fixtures/fiber_canned_responses.json
git commit -m "test: add CSV + Fiber response fixtures for tiny dataset"
```

---

## Task 5: Company-name normalization (pure function)

**Files:**
- Create: `netcrm/normalize.py`
- Create: `tests/test_normalize.py`

- [ ] **Step 1: Write the failing test**

`tests/test_normalize.py`:
```python
import pytest
from netcrm.normalize import company_key

@pytest.mark.parametrize("raw,expected", [
    ("Acme Inc.", "acme"),
    ("ACME, Inc", "acme"),
    ("Acme Inc", "acme"),
    ("acme   inc.", "acme"),
    ("Initech Ltd.", "initech"),
    ("Globex GmbH", "globex"),
    ("Nuts & Bolts AI", "nuts and bolts ai"),
    ("Nuts and Bolts AI", "nuts and bolts ai"),
    ("Cogent Labs Ltd.", "cogent labs"),
    ("Massive Dynamic", "massive dynamic"),
    ("Stark Industries", "stark industries"),
    ("  Wayne Enterprises  ", "wayne enterprises"),
    ("Foo, LLC", "foo"),
    ("Foo, B.V.", "foo"),
    ("Foo, S.A.", "foo"),
    ("Foo S.r.l.", "foo"),
    ("Foo plc", "foo"),
    ("", ""),
    (None, ""),
])
def test_company_key(raw, expected):
    assert company_key(raw) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_normalize.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

`netcrm/normalize.py`:
```python
"""Pure functions for normalizing company names into stable keys."""
from __future__ import annotations
import re

# Legal/corporate suffixes to strip (regex-escaped patterns, longest first)
_SUFFIX_PATTERNS = [
    r",?\s*inc\.?",
    r",?\s*incorporated",
    r",?\s*ltd\.?",
    r",?\s*limited",
    r",?\s*llc",
    r",?\s*l\.l\.c\.?",
    r",?\s*gmbh",
    r",?\s*plc",
    r",?\s*s\.?a\.?",
    r",?\s*s\.?r\.?l\.?",
    r",?\s*b\.?v\.?",
    r",?\s*co\.?",
    r",?\s*corp\.?",
    r",?\s*corporation",
    r",?\s*company",
    r",?\s*pty\.?",
]
_SUFFIX_RE = re.compile(
    r"(?:" + "|".join(_SUFFIX_PATTERNS) + r")\s*$",
    flags=re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[.,]+")


def company_key(raw: str | None) -> str:
    """Normalize a raw company name into a stable, comparable key."""
    if not raw:
        return ""
    s = raw.strip().lower()
    # & → " and " (spaces collapsed below)
    s = s.replace("&", " and ")
    # strip suffixes (run until stable; "Foo Inc Ltd" → "Foo")
    while True:
        new = _SUFFIX_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    # strip residual punctuation, collapse whitespace
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s).strip()
    return s
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_normalize.py -v`
Expected: PASS, all parametrize cases.

- [ ] **Step 5: Commit**

```bash
git add netcrm/normalize.py tests/test_normalize.py
git commit -m "feat(normalize): company-name normalization to stable key"
```

---

## Task 6: CSV ingest

**Files:**
- Create: `netcrm/ingest.py`
- Create: `tests/test_ingest.py`

- [ ] **Step 1: Write the failing test**

`tests/test_ingest.py`:
```python
from datetime import date
from pathlib import Path
import sqlite3
import pytest

from netcrm import db, ingest

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "tiny_connections.csv"
REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def initialized_db(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    return conn


def test_parse_row_basic():
    row = {
        "First Name": "Alice",
        "Last Name": "Smith",
        "URL": "https://www.linkedin.com/in/alice-smith-1",
        "Email Address": "alice@example.com",
        "Company": "Acme Inc.",
        "Position": "VP of Sales",
        "Connected On": "15 May 2026",
    }
    parsed = ingest.parse_row(row)
    assert parsed["linkedin_url"] == "https://www.linkedin.com/in/alice-smith-1"
    assert parsed["first_name"] == "Alice"
    assert parsed["connected_on"] == date(2026, 5, 15)
    assert parsed["company_key"] == "acme"
    assert parsed["raw_company"] == "Acme Inc."


def test_parse_row_missing_optional_fields():
    row = {
        "First Name": "Frank",
        "Last Name": "Brown",
        "URL": "https://www.linkedin.com/in/frank-brown-6",
        "Email Address": "",
        "Company": "",
        "Position": "Consultant",
        "Connected On": "05 Aug 2023",
    }
    parsed = ingest.parse_row(row)
    assert parsed["email"] == "" or parsed["email"] is None
    assert parsed["raw_company"] == "" or parsed["raw_company"] is None
    assert parsed["company_key"] == ""


def test_ingest_csv_skips_preamble_and_loads_rows(initialized_db):
    ingest.ingest_csv(initialized_db, FIXTURE_CSV)
    n = initialized_db.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    assert n == 20


def test_ingest_csv_is_idempotent(initialized_db):
    ingest.ingest_csv(initialized_db, FIXTURE_CSV)
    ingest.ingest_csv(initialized_db, FIXTURE_CSV)
    n = initialized_db.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    assert n == 20


def test_ingest_csv_rejects_unexpected_headers(initialized_db, tmp_path: Path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Foo,Bar,Baz\nx,y,z\n")
    with pytest.raises(ValueError, match="unexpected"):
        ingest.ingest_csv(initialized_db, bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ingest.py -v`
Expected: FAIL — `netcrm.ingest` module not found.

- [ ] **Step 3: Write minimal implementation**

`netcrm/ingest.py`:
```python
"""LinkedIn connections.csv → people table."""
from __future__ import annotations
import csv
import hashlib
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from netcrm.normalize import company_key

EXPECTED_HEADERS = [
    "First Name", "Last Name", "URL", "Email Address",
    "Company", "Position", "Connected On",
]


def _find_header_row(lines: list[str]) -> int:
    """LinkedIn export prefixes the CSV with a 'Notes:' preamble and a blank line."""
    for i, line in enumerate(lines):
        if line.startswith("First Name,"):
            return i
    raise ValueError("could not locate CSV header row 'First Name,...'")


def parse_row(row: dict[str, str]) -> dict:
    raw_company = (row.get("Company") or "").strip()
    raw_position = (row.get("Position") or "").strip()
    raw_date = (row.get("Connected On") or "").strip()
    parsed_date: date | None = None
    if raw_date:
        parsed_date = datetime.strptime(raw_date, "%d %b %Y").date()
    return {
        "linkedin_url": (row.get("URL") or "").strip(),
        "first_name":   (row.get("First Name") or "").strip(),
        "last_name":    (row.get("Last Name") or "").strip(),
        "email":        (row.get("Email Address") or "").strip(),
        "raw_company":  raw_company,
        "raw_position": raw_position,
        "connected_on": parsed_date,
        "company_key":  company_key(raw_company),
    }


def ingest_csv(conn: sqlite3.Connection, csv_path: str | Path) -> int:
    """Insert (or replace) rows from a LinkedIn export CSV. Returns rows ingested."""
    csv_path = Path(csv_path)
    raw_bytes = csv_path.read_bytes()
    sha = hashlib.sha256(raw_bytes).hexdigest()
    text = raw_bytes.decode("utf-8-sig")  # handles BOM
    lines = text.splitlines(keepends=True)
    header_idx = _find_header_row(lines)
    data_text = "".join(lines[header_idx:])

    reader = csv.DictReader(data_text.splitlines())
    if reader.fieldnames is None or set(reader.fieldnames) != set(EXPECTED_HEADERS):
        raise ValueError(
            f"unexpected CSV headers: got {reader.fieldnames!r}, "
            f"expected {EXPECTED_HEADERS!r}"
        )

    now = datetime.now(timezone.utc).isoformat()
    rows = [parse_row(r) for r in reader]
    rows = [r for r in rows if r["linkedin_url"]]  # drop rows missing URL

    with conn:
        conn.executemany(
            """
            INSERT INTO people(
              linkedin_url, first_name, last_name, email,
              raw_company, raw_position, connected_on, company_key,
              imported_at, source_csv_sha
            ) VALUES (
              :linkedin_url, :first_name, :last_name, :email,
              :raw_company, :raw_position, :connected_on, :company_key,
              :imported_at, :source_csv_sha
            )
            ON CONFLICT(linkedin_url) DO UPDATE SET
              first_name=excluded.first_name,
              last_name=excluded.last_name,
              email=excluded.email,
              raw_company=excluded.raw_company,
              raw_position=excluded.raw_position,
              connected_on=excluded.connected_on,
              company_key=excluded.company_key
            """,
            [{**r, "imported_at": now, "source_csv_sha": sha} for r in rows],
        )
    return len(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_ingest.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add netcrm/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): parse LinkedIn CSV into people table (idempotent upsert)"
```

---

## Task 7: Dedupe companies

**Files:**
- Create: `netcrm/companies.py`
- Create: `tests/test_companies.py`

- [ ] **Step 1: Write the failing test**

`tests/test_companies.py`:
```python
from pathlib import Path
import sqlite3
import pytest

from netcrm import db, ingest, companies

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"


@pytest.fixture
def populated_db(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    ingest.ingest_csv(conn, FIXTURE_CSV)
    return conn


def test_dedupe_companies_creates_one_row_per_key(populated_db):
    n = companies.dedupe_companies(populated_db)
    rows = populated_db.execute(
        "SELECT company_key, display_name FROM companies ORDER BY company_key"
    ).fetchall()
    keys = [r["company_key"] for r in rows]
    # 'acme' merges 3 variants; 'stark industries' merges 2; 'globex' merges 2; 'nuts and bolts ai' merges 2
    # Plus: initech, umbrella corp, soylent, tesla, massive dynamic, wayne enterprises, sequoia capital, cogent labs
    # Plus blank "" key for the 2 rows with no company
    assert "acme" in keys
    assert "stark industries" in keys
    assert "globex" in keys
    assert "nuts and bolts ai" in keys
    assert n == len(keys)
    # display_name preserves a real raw value (first-seen)
    acme_row = next(r for r in rows if r["company_key"] == "acme")
    assert acme_row["display_name"] in {"Acme Inc.", "ACME, Inc", "Acme Inc"}


def test_dedupe_companies_is_idempotent(populated_db):
    companies.dedupe_companies(populated_db)
    n_before = populated_db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    companies.dedupe_companies(populated_db)
    n_after = populated_db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    assert n_before == n_after


def test_dedupe_companies_skips_blank_key(populated_db):
    """Two rows have no Company; they should not produce a '' companies row."""
    companies.dedupe_companies(populated_db)
    blank = populated_db.execute(
        "SELECT COUNT(*) FROM companies WHERE company_key = ''"
    ).fetchone()[0]
    assert blank == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_companies.py -v`
Expected: FAIL — `netcrm.companies` module not found.

- [ ] **Step 3: Write minimal implementation**

`netcrm/companies.py`:
```python
"""Companies stage: dedupe normalized company keys + enrich via Fiber."""
from __future__ import annotations
import sqlite3


def dedupe_companies(conn: sqlite3.Connection) -> int:
    """Populate the companies table from distinct people.company_key.

    For each key, picks the first-seen raw_company (lowest rowid) as display_name.
    Existing companies rows are left untouched (we never overwrite display_name
    or enrichment fields here).
    """
    rows = conn.execute(
        """
        SELECT p.company_key, p.raw_company
        FROM people p
        WHERE p.company_key != ''
          AND p.rowid = (
            SELECT MIN(p2.rowid) FROM people p2
            WHERE p2.company_key = p.company_key AND p2.raw_company != ''
          )
        """
    ).fetchall()
    with conn:
        conn.executemany(
            """
            INSERT INTO companies(company_key, display_name)
            VALUES (?, ?)
            ON CONFLICT(company_key) DO NOTHING
            """,
            [(r["company_key"], r["raw_company"]) for r in rows],
        )
    return conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_companies.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add netcrm/companies.py tests/test_companies.py
git commit -m "feat(companies): dedupe people.company_key into companies table"
```

---

## Task 8: Cost tracker

**Files:**
- Create: `netcrm/cost.py`
- Create: `tests/test_cost.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cost.py`:
```python
from pathlib import Path
import sqlite3
import pytest

from netcrm import db, cost

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def conn(tmp_db_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_db_path)
    db.apply_migrations(c, REPO_ROOT / "migrations")
    return c


def test_log_call_writes_row(conn):
    tracker = cost.CostTracker(conn, max_spend_usd=None)
    tracker.log(provider="fiber", operation="org_enrich", units=1,
                usd_cost=0.02, context="acme")
    rows = conn.execute("SELECT provider, usd_cost, context FROM costs").fetchall()
    assert len(rows) == 1
    assert rows[0]["provider"] == "fiber"
    assert rows[0]["usd_cost"] == pytest.approx(0.02)
    assert rows[0]["context"] == "acme"


def test_running_total_tracks_spend(conn):
    tracker = cost.CostTracker(conn, max_spend_usd=None)
    tracker.log("fiber", "org_enrich", 1, 0.02, "a")
    tracker.log("fiber", "org_enrich", 1, 0.03, "b")
    assert tracker.spent_usd == pytest.approx(0.05)


def test_max_spend_aborts_when_exceeded(conn):
    tracker = cost.CostTracker(conn, max_spend_usd=0.05)
    tracker.log("fiber", "org_enrich", 1, 0.04, "a")
    with pytest.raises(cost.SpendCapExceeded):
        tracker.log("fiber", "org_enrich", 1, 0.02, "b")


def test_estimate_fiber_calls():
    # 100 unenriched companies at $0.02/credit, 1 credit each
    est = cost.estimate_fiber(unenriched_count=100, usd_per_credit=0.02,
                              credits_per_call=1)
    assert est == pytest.approx(2.00)


def test_estimate_haiku_calls():
    # 4800 people in batches of 50 = 96 calls.
    # Per-call ~600 input toks + 200 output toks.
    # Use a passed-in price model so we don't hardcode rates here.
    est = cost.estimate_haiku(
        unclassified_count=4800, batch_size=50,
        input_tokens_per_call=600, output_tokens_per_call=200,
        usd_per_input_mtok=1.00, usd_per_output_mtok=5.00,
    )
    # 96 calls * (600*1e-6*1.0 + 200*1e-6*5.0) = 96 * (0.0006 + 0.001) = 96 * 0.0016 = 0.1536
    assert est == pytest.approx(0.1536)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cost.py -v`
Expected: FAIL — `netcrm.cost` not found.

- [ ] **Step 3: Write minimal implementation**

`netcrm/cost.py`:
```python
"""Cost tracking: per-call log, running total, spend cap, dry-run estimators."""
from __future__ import annotations
import math
import sqlite3
from datetime import datetime, timezone


class SpendCapExceeded(RuntimeError):
    pass


class CostTracker:
    def __init__(self, conn: sqlite3.Connection, max_spend_usd: float | None):
        self.conn = conn
        self.max_spend_usd = max_spend_usd
        self.spent_usd: float = 0.0

    def log(self, provider: str, operation: str, units: int,
            usd_cost: float, context: str | None = None) -> None:
        next_total = self.spent_usd + usd_cost
        if self.max_spend_usd is not None and next_total > self.max_spend_usd:
            raise SpendCapExceeded(
                f"spend cap ${self.max_spend_usd:.2f} would be exceeded "
                f"(current ${self.spent_usd:.2f} + ${usd_cost:.4f} = ${next_total:.2f})"
            )
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO costs(ts, provider, operation, units, usd_cost, context)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (datetime.now(timezone.utc).isoformat(),
                 provider, operation, units, usd_cost, context),
            )
        self.spent_usd = next_total


def estimate_fiber(unenriched_count: int, usd_per_credit: float,
                   credits_per_call: int = 1) -> float:
    return unenriched_count * credits_per_call * usd_per_credit


def estimate_haiku(unclassified_count: int, batch_size: int,
                   input_tokens_per_call: int, output_tokens_per_call: int,
                   usd_per_input_mtok: float, usd_per_output_mtok: float) -> float:
    n_calls = math.ceil(unclassified_count / batch_size)
    per_call = (input_tokens_per_call * usd_per_input_mtok / 1_000_000 +
                output_tokens_per_call * usd_per_output_mtok / 1_000_000)
    return n_calls * per_call
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cost.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add netcrm/cost.py tests/test_cost.py
git commit -m "feat(cost): CostTracker with spend cap + dry-run estimators"
```

---

## Task 9: Fiber HTTP client

**Files:**
- Create: `netcrm/fiber.py`
- Create: `tests/test_fiber.py`

> **Note on Fiber API shape:** The Fiber AI organization-enrichment endpoint URL, payload, and response shape may differ from what's coded below. Treat the response-mapping in `_to_enrichment` as the integration point: adjust field names there to match Fiber's actual JSON, leaving the public `FiberClient.enrich(name)` interface unchanged.

- [ ] **Step 1: Write the failing test**

`tests/test_fiber.py`:
```python
import httpx
import pytest
import respx

from netcrm.fiber import FiberClient, FiberStatus


@pytest.fixture
def fiber_client():
    return FiberClient(api_key="test-key", base_url="https://api.fiber.test")


@respx.mock
def test_enrich_returns_ok_on_match(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        return_value=httpx.Response(200, json={
            "industry": "Software",
            "sub_industry": "SaaS",
            "employee_band": "51-200",
            "revenue_band": "$10M-$50M",
            "funding_stage": "Series B",
            "hq_country": "US",
            "hq_region": "California",
            "website": "https://example.com",
            "description": "A company.",
        })
    )
    result = fiber_client.enrich("Acme Inc")
    assert result.status == FiberStatus.OK
    assert result.industry == "Software"
    assert result.employee_band == "51-200"


@respx.mock
def test_enrich_returns_not_found_on_404(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    result = fiber_client.enrich("Nonexistent Co")
    assert result.status == FiberStatus.NOT_FOUND


@respx.mock
def test_enrich_returns_error_on_500(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    result = fiber_client.enrich("Anything")
    assert result.status == FiberStatus.ERROR


@respx.mock
def test_enrich_returns_permanent_error_on_400(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        return_value=httpx.Response(400, json={"error": "malformed"})
    )
    result = fiber_client.enrich("???")
    assert result.status == FiberStatus.PERMANENT_ERROR


@respx.mock
def test_enrich_reports_units_consumed(fiber_client):
    respx.post("https://api.fiber.test/v1/organizations/enrich").mock(
        return_value=httpx.Response(200, headers={"x-fiber-credits": "2"}, json={
            "industry": "X"
        })
    )
    result = fiber_client.enrich("Acme")
    assert result.units == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fiber.py -v`
Expected: FAIL — `netcrm.fiber` not found.

- [ ] **Step 3: Write minimal implementation**

`netcrm/fiber.py`:
```python
"""Thin HTTP client for Fiber AI's organization enrichment endpoint.

The mapping from JSON response → FiberEnrichment lives in `_to_enrichment`.
Adjust field names there to match Fiber's actual response shape; the public
`FiberClient.enrich(name)` interface should stay stable.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx


class FiberStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    ERROR = "error"
    PERMANENT_ERROR = "permanent_error"


@dataclass
class FiberEnrichment:
    status: FiberStatus
    units: int = 0
    industry: str | None = None
    sub_industry: str | None = None
    employee_band: str | None = None
    revenue_band: str | None = None
    funding_stage: str | None = None
    hq_country: str | None = None
    hq_region: str | None = None
    website: str | None = None
    description: str | None = None


class FiberClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def enrich(self, name: str) -> FiberEnrichment:
        try:
            r = self.client.post(
                f"{self.base_url}/v1/organizations/enrich",
                json={"name": name},
            )
        except httpx.HTTPError:
            return FiberEnrichment(status=FiberStatus.ERROR)

        units = int(r.headers.get("x-fiber-credits", "1") or "1")

        if r.status_code == 200:
            return self._to_enrichment(r.json(), units=units)
        if r.status_code == 404:
            return FiberEnrichment(status=FiberStatus.NOT_FOUND, units=units)
        if 400 <= r.status_code < 500:
            return FiberEnrichment(status=FiberStatus.PERMANENT_ERROR, units=units)
        return FiberEnrichment(status=FiberStatus.ERROR, units=units)

    @staticmethod
    def _to_enrichment(payload: dict[str, Any], units: int) -> FiberEnrichment:
        return FiberEnrichment(
            status=FiberStatus.OK,
            units=units,
            industry=payload.get("industry"),
            sub_industry=payload.get("sub_industry"),
            employee_band=payload.get("employee_band"),
            revenue_band=payload.get("revenue_band"),
            funding_stage=payload.get("funding_stage"),
            hq_country=payload.get("hq_country"),
            hq_region=payload.get("hq_region"),
            website=payload.get("website"),
            description=payload.get("description"),
        )

    def close(self) -> None:
        self.client.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fiber.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Add a gated live integration test**

Append to `tests/test_fiber.py`:
```python
import os


@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_TESTS"),
    reason="set RUN_LIVE_TESTS=1 to hit real Fiber",
)
def test_live_enrich_well_known_company():
    """Sanity: real Fiber returns an industry for a well-known company.

    Costs one Fiber credit per run. Run manually before shipping changes that
    touch fiber.py to confirm the API contract still matches what we map.
    """
    api_key = os.environ.get("FIBER_API_KEY")
    base_url = os.environ.get("FIBER_API_BASE_URL", "https://api.fiberai.com")
    assert api_key, "FIBER_API_KEY required for RUN_LIVE_TESTS"
    client = FiberClient(api_key=api_key, base_url=base_url)
    try:
        result = client.enrich("Stripe")
        assert result.status == FiberStatus.OK
        assert result.industry  # non-empty
    finally:
        client.close()
```

Run: `RUN_LIVE_TESTS=1 FIBER_API_KEY=<your-key> pytest tests/test_fiber.py::test_live_enrich_well_known_company -v`
Expected when key present: PASS. Without the env var: SKIPPED.

- [ ] **Step 6: Commit**

```bash
git add netcrm/fiber.py tests/test_fiber.py
git commit -m "feat(fiber): HTTP client + gated live integration test"
```

---

## Task 10: Enrich companies stage (Fiber + cost + cache)

**Files:**
- Modify: `netcrm/companies.py` (append `enrich_companies`)
- Create: `netcrm/_stubs.py` (StubFiberClient for in-memory testing)
- Modify: `tests/test_companies.py` (append enrich tests)

- [ ] **Step 1: Create `netcrm/_stubs.py` with StubFiberClient**

`netcrm/_stubs.py`:
```python
"""In-memory stubs used by tests AND by the CLI when NETCRM_TEST_MODE=1.

Lives in the netcrm package (not under tests/) so the prod CLI can import it
without depending on pytest being installed.
"""
from __future__ import annotations
import json
from pathlib import Path

from netcrm.fiber import FiberEnrichment, FiberStatus
from netcrm.normalize import company_key


class StubFiberClient:
    """Returns canned Fiber responses keyed on normalized company_key.

    Companies not in the canned data return FiberStatus.NOT_FOUND.
    """
    def __init__(self, canned: dict[str, dict]):
        self.canned = canned
        self.calls: list[str] = []

    def enrich(self, name: str) -> FiberEnrichment:
        self.calls.append(name)
        key = company_key(name)
        if key in self.canned:
            return FiberEnrichment(status=FiberStatus.OK, units=1, **self.canned[key])
        return FiberEnrichment(status=FiberStatus.NOT_FOUND, units=1)


def load_canned_fiber_stub(fixtures_dir: Path) -> StubFiberClient:
    """Convenience: load the JSON fixture and return a stub."""
    canned = json.loads((fixtures_dir / "fiber_canned_responses.json").read_text())
    return StubFiberClient(canned)
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_companies.py`:
```python
import json
from netcrm.fiber import FiberClient, FiberEnrichment, FiberStatus
from netcrm.cost import CostTracker
from netcrm._stubs import StubFiberClient


@pytest.fixture
def stub_fiber() -> StubFiberClient:
    canned_path = REPO_ROOT / "tests" / "fixtures" / "fiber_canned_responses.json"
    return StubFiberClient(json.loads(canned_path.read_text()))


def test_enrich_companies_writes_ok_rows(populated_db, stub_fiber):
    companies.dedupe_companies(populated_db)
    tracker = CostTracker(populated_db, max_spend_usd=None)
    companies.enrich_companies(populated_db, stub_fiber, tracker,
                               usd_per_credit=0.02)
    row = populated_db.execute(
        "SELECT industry, employee_band, fiber_status FROM companies "
        "WHERE company_key = 'acme'"
    ).fetchone()
    assert row["fiber_status"] == "ok"
    assert row["industry"] == "Manufacturing"


def test_enrich_companies_writes_not_found(populated_db, stub_fiber):
    companies.dedupe_companies(populated_db)
    tracker = CostTracker(populated_db, max_spend_usd=None)
    companies.enrich_companies(populated_db, stub_fiber, tracker,
                               usd_per_credit=0.02)
    # 'umbrella corp' is not in canned responses
    row = populated_db.execute(
        "SELECT industry, fiber_status FROM companies "
        "WHERE company_key = 'umbrella corp'"
    ).fetchone()
    assert row["fiber_status"] == "not_found"
    assert row["industry"] is None


def test_enrich_companies_skips_already_enriched(populated_db, stub_fiber):
    companies.dedupe_companies(populated_db)
    tracker = CostTracker(populated_db, max_spend_usd=None)
    companies.enrich_companies(populated_db, stub_fiber, tracker, usd_per_credit=0.02)
    n_calls_first = len(stub_fiber.calls)
    companies.enrich_companies(populated_db, stub_fiber, tracker, usd_per_credit=0.02)
    n_calls_second = len(stub_fiber.calls)
    assert n_calls_second == n_calls_first  # nothing re-enriched


def test_enrich_companies_logs_costs(populated_db, stub_fiber):
    companies.dedupe_companies(populated_db)
    tracker = CostTracker(populated_db, max_spend_usd=None)
    companies.enrich_companies(populated_db, stub_fiber, tracker, usd_per_credit=0.02)
    n_cost_rows = populated_db.execute(
        "SELECT COUNT(*) FROM costs WHERE provider='fiber'"
    ).fetchone()[0]
    n_companies = populated_db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    # Every enrichment attempt logs one cost row (ok + not_found both cost a credit)
    assert n_cost_rows == n_companies
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_companies.py::test_enrich_companies_writes_ok_rows -v`
Expected: FAIL — `companies.enrich_companies` not defined.

- [ ] **Step 4: Append implementation to `netcrm/companies.py`**

```python
from datetime import datetime, timezone
from typing import Protocol

from netcrm.cost import CostTracker
from netcrm.fiber import FiberClient, FiberEnrichment, FiberStatus


class _FiberLike(Protocol):
    def enrich(self, name: str) -> FiberEnrichment: ...


def count_unenriched(conn: sqlite3.Connection) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM companies
        WHERE fiber_enriched_at IS NULL
          AND (fiber_status IS NULL OR fiber_status = 'error')
        """
    ).fetchone()[0]


def enrich_companies(
    conn: sqlite3.Connection,
    fiber: _FiberLike,
    cost: CostTracker,
    usd_per_credit: float,
) -> int:
    """Enrich every un-enriched company. Returns number of API calls made."""
    rows = conn.execute(
        """
        SELECT company_key, display_name FROM companies
        WHERE fiber_enriched_at IS NULL
          AND (fiber_status IS NULL OR fiber_status = 'error')
        ORDER BY company_key
        """
    ).fetchall()
    n_calls = 0
    now_iso = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731
    for row in rows:
        result: FiberEnrichment = fiber.enrich(row["display_name"])
        n_calls += 1
        cost.log(
            provider="fiber", operation="org_enrich",
            units=result.units, usd_cost=result.units * usd_per_credit,
            context=row["company_key"],
        )
        with conn:
            conn.execute(
                """
                UPDATE companies SET
                  industry=?, sub_industry=?, employee_band=?, revenue_band=?,
                  funding_stage=?, hq_country=?, hq_region=?, website=?,
                  description=?,
                  fiber_enriched_at=?, fiber_status=?
                WHERE company_key=?
                """,
                (
                    result.industry, result.sub_industry, result.employee_band,
                    result.revenue_band, result.funding_stage,
                    result.hq_country, result.hq_region, result.website,
                    result.description,
                    # error: leave fiber_enriched_at NULL so we retry next run
                    None if result.status == FiberStatus.ERROR else now_iso(),
                    result.status.value,
                    row["company_key"],
                ),
            )
    return n_calls
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_companies.py -v`
Expected: PASS, 7 tests total (3 dedupe + 4 enrich).

- [ ] **Step 6: Commit**

```bash
git add netcrm/_stubs.py netcrm/companies.py tests/test_companies.py
git commit -m "feat(companies): enrich_companies via Fiber with cache-skip + cost log"
```

---

## Task 11: Anthropic batched classifier client

**Files:**
- Create: `netcrm/anthropic_client.py`
- Create: `tests/test_anthropic_client.py`

- [ ] **Step 1: Write the failing test**

`tests/test_anthropic_client.py`:
```python
from unittest.mock import MagicMock

from netcrm.anthropic_client import ClassifierClient, ClassificationRequest


def _make_fake_anthropic(tool_input: list[dict], in_tok: int = 600, out_tok: int = 200):
    """Return a mock anthropic client returning a single tool_use block."""
    fake = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.name = "classify_people"
    block.input = {"classifications": tool_input}
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=in_tok, output_tokens=out_tok)
    fake.messages.create.return_value = response
    return fake


def test_classify_batch_parses_tool_use_output():
    fake = _make_fake_anthropic([
        {"linkedin_url": "u1", "role_bucket": "Sales", "seniority": "VP"},
        {"linkedin_url": "u2", "role_bucket": "Marketing", "seniority": "Director"},
    ])
    client = ClassifierClient(fake, model="claude-haiku-4-5-20251001")
    requests = [
        ClassificationRequest(linkedin_url="u1", raw_position="VP Sales", raw_company="Acme"),
        ClassificationRequest(linkedin_url="u2", raw_position="Director Marketing", raw_company="Globex"),
    ]
    result = client.classify_batch(requests)
    assert result.classifications[0]["role_bucket"] == "Sales"
    assert result.input_tokens == 600
    assert result.output_tokens == 200


def test_classify_batch_defaults_missing_fields():
    fake = _make_fake_anthropic([
        {"linkedin_url": "u1"},   # missing role_bucket and seniority
    ])
    client = ClassifierClient(fake, model="claude-haiku-4-5-20251001")
    requests = [ClassificationRequest(linkedin_url="u1",
                                      raw_position="???",
                                      raw_company="")]
    result = client.classify_batch(requests)
    assert result.classifications[0]["role_bucket"] == "Other"
    assert result.classifications[0]["seniority"] == "Unknown"


def test_classify_batch_clamps_invalid_enums():
    fake = _make_fake_anthropic([
        {"linkedin_url": "u1", "role_bucket": "WeirdValue", "seniority": "WhoKnows"},
    ])
    client = ClassifierClient(fake, model="claude-haiku-4-5-20251001")
    result = client.classify_batch([ClassificationRequest(linkedin_url="u1",
                                                          raw_position="x",
                                                          raw_company="y")])
    assert result.classifications[0]["role_bucket"] == "Other"
    assert result.classifications[0]["seniority"] == "Unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_anthropic_client.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

`netcrm/anthropic_client.py`:
```python
"""Batched Haiku classifier using tool-use JSON output."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

VALID_ROLE_BUCKETS = {
    "Sales", "BD", "Marketing", "Engineering", "Product", "Design",
    "Founder", "Investor", "Operations", "Consulting", "Student", "Other",
}
VALID_SENIORITY = {
    "IC", "Manager", "Director", "VP", "C-suite", "Founder", "Unknown",
}

TOOL_SCHEMA = {
    "name": "classify_people",
    "description": "Classify each person into a role bucket and seniority.",
    "input_schema": {
        "type": "object",
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "linkedin_url": {"type": "string"},
                        "role_bucket": {
                            "type": "string",
                            "enum": sorted(VALID_ROLE_BUCKETS),
                        },
                        "seniority": {
                            "type": "string",
                            "enum": sorted(VALID_SENIORITY),
                        },
                    },
                    "required": ["linkedin_url", "role_bucket", "seniority"],
                },
            }
        },
        "required": ["classifications"],
    },
}

SYSTEM_PROMPT = """\
You classify LinkedIn connections into role_bucket and seniority based on the \
person's current job title and company. Return ONE classification per input \
person via the `classify_people` tool. Rules:

- role_bucket exactly one of: Sales, BD, Marketing, Engineering, Product, \
Design, Founder, Investor, Operations, Consulting, Student, Other.
- seniority exactly one of: IC, Manager, Director, VP, C-suite, Founder, Unknown.
- If the title contains 'Founder', 'Co-founder', or 'Owner', use role_bucket=Founder \
and seniority=Founder regardless of other words.
- 'Founding [X]' (e.g. Founding Engineer): keep the X-derived role_bucket but set \
seniority=Founder.
- 'Head of [X]': seniority=Director by default, VP for large companies. Use the \
company name as a hint when available.
- 'Chief X Officer' → seniority=C-suite.
- 'VP of X' / 'Vice President X' → seniority=VP.
- 'Director of X' → seniority=Director.
- 'Manager' / 'Lead' (without Director/VP/Chief) → seniority=Manager.
- Plain IC titles (Engineer, AE, SDR, Designer) → seniority=IC.
- Multi-titled people: use the FIRST title clause only.
- If you genuinely cannot tell: role_bucket=Other, seniority=Unknown.
"""


@dataclass
class ClassificationRequest:
    linkedin_url: str
    raw_position: str
    raw_company: str


@dataclass
class ClassificationBatchResult:
    classifications: list[dict[str, str]]
    input_tokens: int
    output_tokens: int


class ClassifierClient:
    def __init__(self, anthropic_client: Any, model: str):
        self.client = anthropic_client
        self.model = model

    def classify_batch(
        self, requests: list[ClassificationRequest],
    ) -> ClassificationBatchResult:
        if not requests:
            return ClassificationBatchResult([], 0, 0)
        user_lines = [
            f"- url={r.linkedin_url} | position={r.raw_position!r} | "
            f"company={r.raw_company!r}"
            for r in requests
        ]
        user_msg = (
            "Classify each of the following people. Return one entry per "
            "url in the same order.\n\n" + "\n".join(user_lines)
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "classify_people"},
            messages=[{"role": "user", "content": user_msg}],
        )
        tool_block = next(
            (b for b in response.content
             if getattr(b, "type", "") == "tool_use"
             and getattr(b, "name", "") == "classify_people"),
            None,
        )
        raw = tool_block.input.get("classifications", []) if tool_block else []
        cleaned: list[dict[str, str]] = []
        url_order = [r.linkedin_url for r in requests]
        by_url = {item.get("linkedin_url"): item for item in raw if isinstance(item, dict)}
        for url in url_order:
            item = by_url.get(url, {})
            role = item.get("role_bucket", "Other")
            sen = item.get("seniority", "Unknown")
            if role not in VALID_ROLE_BUCKETS:
                role = "Other"
            if sen not in VALID_SENIORITY:
                sen = "Unknown"
            cleaned.append({
                "linkedin_url": url,
                "role_bucket": role,
                "seniority": sen,
            })
        return ClassificationBatchResult(
            classifications=cleaned,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_anthropic_client.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add netcrm/anthropic_client.py tests/test_anthropic_client.py
git commit -m "feat(anthropic): batched classifier client using tool-use JSON output"
```

---

## Task 12: Classify-people stage

**Files:**
- Create: `netcrm/classify.py`
- Modify: `netcrm/_stubs.py` (append `StubClassifierClient`)
- Create: `tests/test_classify.py`

- [ ] **Step 1: Append `StubClassifierClient` to `netcrm/_stubs.py`**

Add to `netcrm/_stubs.py`:
```python
from netcrm.anthropic_client import ClassificationBatchResult


class StubClassifierClient:
    """Returns deterministic classifications keyed on words in raw_position."""
    def __init__(self):
        self.batches_called = 0

    def classify_batch(self, requests):
        self.batches_called += 1
        out = []
        for r in requests:
            pos = (r.raw_position or "").lower()
            role = "Other"
            sen = "Unknown"
            if "founder" in pos or "co-founder" in pos:
                role, sen = "Founder", "Founder"
            elif "sales" in pos or "account executive" in pos:
                role, sen = "Sales", "VP" if "vp" in pos else "IC"
            elif "marketing" in pos or "growth" in pos:
                role, sen = "Marketing", "Director" if "director" in pos else "IC"
            elif "engineer" in pos or "engineering" in pos:
                role, sen = "Engineering", "VP" if "vp" in pos else "IC"
                if "founding" in pos:
                    sen = "Founder"
            elif "product" in pos:
                role, sen = "Product", "C-suite" if "chief" in pos else "IC"
            elif "principal" in pos:
                role, sen = "Investor", "Director"
            elif "business development" in pos:
                role, sen = "BD", "Manager"
            elif "operations" in pos:
                role, sen = "Operations", "Director"
            elif "consultant" in pos:
                role, sen = "Consulting", "IC"
            elif "student" in pos:
                role, sen = "Student", "IC"
            out.append({"linkedin_url": r.linkedin_url, "role_bucket": role, "seniority": sen})
        return ClassificationBatchResult(out, input_tokens=600, output_tokens=200)
```

- [ ] **Step 2: Write the failing test**

`tests/test_classify.py`:
```python
from pathlib import Path
import sqlite3
import pytest

from netcrm import db, ingest, classify
from netcrm._stubs import StubClassifierClient
from netcrm.cost import CostTracker

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"


@pytest.fixture
def populated_db(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    ingest.ingest_csv(conn, FIXTURE_CSV)
    return conn


def test_classify_people_writes_one_row_per_person(populated_db):
    stub = StubClassifierClient()
    tracker = CostTracker(populated_db, max_spend_usd=None)
    classify.classify_people(
        populated_db, stub, tracker,
        model="claude-haiku-4-5-20251001",
        batch_size=5,
        usd_per_input_mtok=1.0, usd_per_output_mtok=5.0,
    )
    n_class = populated_db.execute("SELECT COUNT(*) FROM people_class").fetchone()[0]
    n_people = populated_db.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    assert n_class == n_people


def test_classify_people_marks_founders(populated_db):
    stub = StubClassifierClient()
    tracker = CostTracker(populated_db, max_spend_usd=None)
    classify.classify_people(populated_db, stub, tracker,
                             model="claude-haiku-4-5-20251001",
                             batch_size=5,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    rows = populated_db.execute(
        """
        SELECT p.first_name, pc.role_bucket, pc.seniority
        FROM people p JOIN people_class pc USING(linkedin_url)
        WHERE pc.role_bucket = 'Founder'
        """
    ).fetchall()
    names = {r["first_name"] for r in rows}
    assert {"Carol", "Pete"}.issubset(names)


def test_classify_people_is_idempotent(populated_db):
    stub = StubClassifierClient()
    tracker = CostTracker(populated_db, max_spend_usd=None)
    classify.classify_people(populated_db, stub, tracker,
                             model="claude-haiku-4-5-20251001",
                             batch_size=5,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    first = stub.batches_called
    classify.classify_people(populated_db, stub, tracker,
                             model="claude-haiku-4-5-20251001",
                             batch_size=5,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    assert stub.batches_called == first


def test_classify_people_batches_correctly(populated_db):
    stub = StubClassifierClient()
    tracker = CostTracker(populated_db, max_spend_usd=None)
    classify.classify_people(populated_db, stub, tracker,
                             model="claude-haiku-4-5-20251001",
                             batch_size=5,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    # 20 people / batch_size=5 → 4 batches
    assert stub.batches_called == 4


def test_classify_people_logs_costs(populated_db):
    stub = StubClassifierClient()
    tracker = CostTracker(populated_db, max_spend_usd=None)
    classify.classify_people(populated_db, stub, tracker,
                             model="claude-haiku-4-5-20251001",
                             batch_size=5,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    n_cost = populated_db.execute(
        "SELECT COUNT(*) FROM costs WHERE provider='anthropic'"
    ).fetchone()[0]
    assert n_cost == 4  # one log row per batch
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_classify.py -v`
Expected: FAIL — `classify.classify_people` not defined.

- [ ] **Step 4: Write minimal implementation**

`netcrm/classify.py`:
```python
"""Classify-people stage: batch un-classified people through the Haiku tool."""
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from typing import Protocol

from netcrm.anthropic_client import (
    ClassificationBatchResult, ClassificationRequest,
)
from netcrm.cost import CostTracker


class _ClassifierLike(Protocol):
    def classify_batch(
        self, requests: list[ClassificationRequest],
    ) -> ClassificationBatchResult: ...


def count_unclassified(conn: sqlite3.Connection) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM people p
        LEFT JOIN people_class pc USING(linkedin_url)
        WHERE pc.linkedin_url IS NULL
        """
    ).fetchone()[0]


def classify_people(
    conn: sqlite3.Connection,
    classifier: _ClassifierLike,
    cost: CostTracker,
    model: str,
    batch_size: int,
    usd_per_input_mtok: float,
    usd_per_output_mtok: float,
) -> int:
    """Classify every un-classified person. Returns number of batches sent."""
    rows = conn.execute(
        """
        SELECT p.linkedin_url, p.raw_position, p.raw_company
        FROM people p
        LEFT JOIN people_class pc USING(linkedin_url)
        WHERE pc.linkedin_url IS NULL
        ORDER BY p.linkedin_url
        """
    ).fetchall()
    n_batches = 0
    for i in range(0, len(rows), batch_size):
        slice_ = rows[i:i + batch_size]
        requests = [
            ClassificationRequest(
                linkedin_url=r["linkedin_url"],
                raw_position=r["raw_position"] or "",
                raw_company=r["raw_company"] or "",
            )
            for r in slice_
        ]
        result = classifier.classify_batch(requests)
        n_batches += 1
        batch_usd = (
            result.input_tokens * usd_per_input_mtok / 1_000_000
            + result.output_tokens * usd_per_output_mtok / 1_000_000
        )
        cost.log(
            provider="anthropic", operation="classify_batch",
            units=result.input_tokens + result.output_tokens,
            usd_cost=batch_usd,
            context=f"batch_size={len(requests)}",
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.executemany(
                """
                INSERT INTO people_class(
                  linkedin_url, role_bucket, seniority,
                  classified_at, classifier_model
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(linkedin_url) DO UPDATE SET
                  role_bucket=excluded.role_bucket,
                  seniority=excluded.seniority,
                  classified_at=excluded.classified_at,
                  classifier_model=excluded.classifier_model
                """,
                [
                    (c["linkedin_url"], c["role_bucket"], c["seniority"],
                     now_iso, model)
                    for c in result.classifications
                ],
            )
    return n_batches
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_classify.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add netcrm/_stubs.py netcrm/classify.py tests/test_classify.py
git commit -m "feat(classify): classify-people stage with batched LLM + cost log"
```

---

## Task 13: Build-views stage

**Files:**
- Create: `netcrm/views.py`
- Create: `tests/test_views.py`

- [ ] **Step 1: Write the failing test**

`tests/test_views.py`:
```python
from pathlib import Path
import sqlite3
import pytest

from netcrm import db, ingest, companies, classify, views
from netcrm.cost import CostTracker
from netcrm._stubs import StubFiberClient, StubClassifierClient, load_canned_fiber_stub

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture
def fully_loaded_db(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    ingest.ingest_csv(conn, FIXTURE_CSV)
    companies.dedupe_companies(conn)
    companies.enrich_companies(conn, load_canned_fiber_stub(FIXTURES_DIR),
                               CostTracker(conn, None), usd_per_credit=0.02)
    classify.classify_people(conn, StubClassifierClient(),
                             CostTracker(conn, None),
                             model="claude-haiku-4-5-20251001",
                             batch_size=10,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    return conn


def test_build_views_creates_people_enriched(fully_loaded_db):
    views.build_views(fully_loaded_db)
    view_names = {
        r[0] for r in fully_loaded_db.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()
    }
    assert "people_enriched" in view_names


def test_people_enriched_joins_company_and_class(fully_loaded_db):
    views.build_views(fully_loaded_db)
    row = fully_loaded_db.execute(
        """
        SELECT first_name, company_name, industry, role_bucket, seniority
        FROM people_enriched
        WHERE first_name = 'Alice'
        """
    ).fetchone()
    assert row["industry"] == "Manufacturing"
    assert row["role_bucket"] == "Sales"
    assert row["seniority"] == "VP"


def test_build_views_is_idempotent(fully_loaded_db):
    views.build_views(fully_loaded_db)
    views.build_views(fully_loaded_db)  # must not raise
    view_names = {
        r[0] for r in fully_loaded_db.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()
    }
    assert "people_enriched" in view_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_views.py -v`
Expected: FAIL — `netcrm.views` not found.

- [ ] **Step 3: Write minimal implementation**

`netcrm/views.py`:
```python
"""Create the people_enriched view and any future read-only views."""
from __future__ import annotations
import sqlite3

PEOPLE_ENRICHED_SQL = """
CREATE VIEW IF NOT EXISTS people_enriched AS
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
"""


def build_views(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executescript(PEOPLE_ENRICHED_SQL)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_views.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add netcrm/views.py tests/test_views.py
git commit -m "feat(views): people_enriched view joining people + company + class"
```

---

## Task 14: Typer CLI wiring

**Files:**
- Create: `netcrm/cli.py`
- Create: `tests/test_cli_smoke.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_smoke.py`:
```python
"""End-to-end smoke test: stub Fiber + stub Anthropic, full pipeline via CLI."""
from pathlib import Path
import json
import os
import sqlite3
import pytest
from typer.testing import CliRunner

from netcrm.cli import app

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"


@pytest.fixture
def smoke_env(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "smoke.db"
    monkeypatch.setenv("NETCRM_DB_PATH", str(db_path))
    monkeypatch.setenv("FIBER_API_KEY", "test")
    monkeypatch.setenv("FIBER_API_BASE_URL", "https://api.fiber.test")
    monkeypatch.setenv("FIBER_USD_PER_CREDIT", "0.02")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("NETCRM_TEST_MODE", "1")  # signal: use stubs
    return db_path


def test_cli_run_all_smoke(smoke_env):
    runner = CliRunner()
    result = runner.invoke(app, ["run-all", str(FIXTURE_CSV)])
    assert result.exit_code == 0, result.stdout
    conn = sqlite3.connect(smoke_env)
    n_people = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    n_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    n_class = conn.execute("SELECT COUNT(*) FROM people_class").fetchone()[0]
    assert n_people == 20
    assert n_companies > 0
    assert n_class == 20
    # view exists
    n_view = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='view' AND name='people_enriched'"
    ).fetchone()[0]
    assert n_view == 1
    conn.close()


def test_cli_dry_run_enrich(smoke_env):
    runner = CliRunner()
    runner.invoke(app, ["ingest", str(FIXTURE_CSV)])
    runner.invoke(app, ["dedupe-companies"])
    result = runner.invoke(app, ["enrich-companies", "--dry-run"])
    assert result.exit_code == 0
    assert "would enrich" in result.stdout.lower()


def test_cli_idempotent(smoke_env):
    runner = CliRunner()
    runner.invoke(app, ["run-all", str(FIXTURE_CSV)])
    conn = sqlite3.connect(smoke_env)
    n_costs_first = conn.execute("SELECT COUNT(*) FROM costs").fetchone()[0]
    conn.close()
    runner.invoke(app, ["run-all", str(FIXTURE_CSV)])
    conn = sqlite3.connect(smoke_env)
    n_costs_second = conn.execute("SELECT COUNT(*) FROM costs").fetchone()[0]
    conn.close()
    assert n_costs_second == n_costs_first  # no new API calls on re-run
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_smoke.py -v`
Expected: FAIL — `netcrm.cli.app` not defined.

- [ ] **Step 3: Write minimal implementation**

`netcrm/cli.py`:
```python
"""Typer entrypoint. Each sub-command maps 1:1 to a pipeline stage."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv

from netcrm import db, ingest, companies, classify, views
from netcrm.cost import CostTracker, SpendCapExceeded, estimate_fiber, estimate_haiku

app = typer.Typer(no_args_is_help=True, add_completion=False)

REPO_ROOT = Path(__file__).parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


# ---------- helpers ----------

def _env(name: str, default: Optional[str] = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise typer.BadParameter(f"missing required env var: {name}")
    return val


def _db_path() -> Path:
    return Path(os.environ.get("NETCRM_DB_PATH", "crm.db"))


def _open_db():
    conn = db.connect(_db_path())
    db.apply_migrations(conn, MIGRATIONS_DIR)
    return conn


def _make_fiber_client():
    """In test mode, return a stub keyed on canned JSON. Otherwise real client."""
    if os.environ.get("NETCRM_TEST_MODE"):
        from netcrm._stubs import load_canned_fiber_stub
        return load_canned_fiber_stub(REPO_ROOT / "tests" / "fixtures")
    from netcrm.fiber import FiberClient
    return FiberClient(api_key=_env("FIBER_API_KEY"),
                       base_url=_env("FIBER_API_BASE_URL"))


def _make_classifier_client():
    if os.environ.get("NETCRM_TEST_MODE"):
        from netcrm._stubs import StubClassifierClient
        return StubClassifierClient()
    import anthropic
    from netcrm.anthropic_client import ClassifierClient
    sdk = anthropic.Anthropic(api_key=_env("ANTHROPIC_API_KEY"))
    return ClassifierClient(sdk, model=_env("ANTHROPIC_MODEL",
                                            "claude-haiku-4-5-20251001"))


# ---------- commands ----------

@app.command(name="ingest")
def ingest_cmd(
    csv_path: Path = typer.Argument(..., exists=True, dir_okay=False,
                                    help="Path to LinkedIn connections.csv"),
):
    """Ingest a LinkedIn connections.csv into the people table."""
    load_dotenv()
    conn = _open_db()
    n = ingest.ingest_csv(conn, csv_path)
    typer.echo(f"ingested {n} people rows")


@app.command(name="dedupe-companies")
def dedupe_companies_cmd():
    """Populate companies table from distinct people.company_key."""
    load_dotenv()
    conn = _open_db()
    n = companies.dedupe_companies(conn)
    typer.echo(f"companies table now has {n} rows")


@app.command(name="enrich-companies")
def enrich_companies_cmd(
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_spend_usd: Optional[float] = typer.Option(None, "--max-spend-usd"),
):
    """Enrich every un-enriched company via Fiber AI."""
    load_dotenv()
    conn = _open_db()
    usd_per_credit = float(os.environ.get("FIBER_USD_PER_CREDIT", "0.02"))
    unenriched = companies.count_unenriched(conn)
    est = estimate_fiber(unenriched, usd_per_credit=usd_per_credit)
    if dry_run:
        typer.echo(f"would enrich {unenriched} companies, estimated cost ${est:.2f}")
        return
    if max_spend_usd is not None and est > max_spend_usd:
        typer.echo(f"estimated ${est:.2f} exceeds --max-spend-usd ${max_spend_usd:.2f}; aborting",
                   err=True)
        raise typer.Exit(code=2)
    fiber = _make_fiber_client()
    tracker = CostTracker(conn, max_spend_usd=max_spend_usd)
    try:
        n = companies.enrich_companies(conn, fiber, tracker, usd_per_credit=usd_per_credit)
    except SpendCapExceeded as e:
        typer.echo(f"aborted: {e}", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"enriched {n} companies, cost ${tracker.spent_usd:.2f}")


@app.command(name="classify-people")
def classify_people_cmd(
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_spend_usd: Optional[float] = typer.Option(None, "--max-spend-usd"),
    batch_size: int = typer.Option(50, "--batch-size"),
):
    """Classify every un-classified person via Haiku."""
    load_dotenv()
    conn = _open_db()
    unclassified = classify.count_unclassified(conn)
    # rough constants used for dry-run estimate
    usd_in = float(os.environ.get("HAIKU_USD_PER_INPUT_MTOK", "1.00"))
    usd_out = float(os.environ.get("HAIKU_USD_PER_OUTPUT_MTOK", "5.00"))
    est = estimate_haiku(unclassified, batch_size=batch_size,
                         input_tokens_per_call=600, output_tokens_per_call=200,
                         usd_per_input_mtok=usd_in,
                         usd_per_output_mtok=usd_out)
    if dry_run:
        typer.echo(f"would classify {unclassified} people in batches of "
                   f"{batch_size}, estimated cost ${est:.4f}")
        return
    if max_spend_usd is not None and est > max_spend_usd:
        typer.echo(f"estimated ${est:.4f} exceeds --max-spend-usd ${max_spend_usd:.2f}",
                   err=True)
        raise typer.Exit(code=2)
    classifier = _make_classifier_client()
    tracker = CostTracker(conn, max_spend_usd=max_spend_usd)
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    try:
        n = classify.classify_people(conn, classifier, tracker,
                                     model=model, batch_size=batch_size,
                                     usd_per_input_mtok=usd_in,
                                     usd_per_output_mtok=usd_out)
    except SpendCapExceeded as e:
        typer.echo(f"aborted: {e}", err=True)
        raise typer.Exit(code=2)
    typer.echo(f"classified in {n} batches, cost ${tracker.spent_usd:.4f}")


@app.command(name="build-views")
def build_views_cmd():
    """(Re-)create people_enriched view."""
    load_dotenv()
    conn = _open_db()
    views.build_views(conn)
    typer.echo("views built")


@app.command(name="run-all")
def run_all_cmd(
    csv_path: Path = typer.Argument(..., exists=True, dir_okay=False),
    max_spend_usd: Optional[float] = typer.Option(None, "--max-spend-usd"),
):
    """Run all stages end-to-end."""
    load_dotenv()
    conn = _open_db()
    ingest.ingest_csv(conn, csv_path)
    companies.dedupe_companies(conn)
    enrich_companies_cmd(dry_run=False, max_spend_usd=max_spend_usd)
    classify_people_cmd(dry_run=False, max_spend_usd=max_spend_usd, batch_size=50)
    views.build_views(conn)
    typer.echo("run-all complete")


@app.command(name="cost-report")
def cost_report_cmd():
    """Print cumulative spend per provider."""
    load_dotenv()
    conn = _open_db()
    rows = conn.execute(
        "SELECT provider, SUM(usd_cost) AS usd FROM costs GROUP BY provider"
    ).fetchall()
    if not rows:
        typer.echo("no costs logged")
        return
    for r in rows:
        typer.echo(f"{r['provider']:12s} ${r['usd']:.4f}")


@app.command(name="serve")
def serve_cmd():
    """Run datasette against crm.db with metadata.yml."""
    import subprocess
    subprocess.run(
        ["datasette", "serve", str(_db_path()),
         "--metadata", str(REPO_ROOT / "metadata.yml")],
        check=False,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_smoke.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add netcrm/cli.py tests/test_cli_smoke.py
git commit -m "feat(cli): typer CLI with ingest/dedupe/enrich/classify/views/run-all"
```

---

## Task 15: Saved queries + Datasette metadata

**Files:**
- Create: `saved_queries/reconnect_targets.sql`
- Create: `saved_queries/sales_at_growth_companies.sql`
- Create: `saved_queries/marketing_leaders.sql`
- Create: `saved_queries/founders_in_target_industries.sql`
- Create: `metadata.yml`
- Create: `tests/test_saved_queries.py`

- [ ] **Step 1: Write the failing test**

`tests/test_saved_queries.py`:
```python
from pathlib import Path
import sqlite3
import pytest
import yaml

from netcrm import db, ingest, companies, classify, views
from netcrm.cost import CostTracker
from netcrm._stubs import StubClassifierClient, load_canned_fiber_stub

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_CSV = REPO_ROOT / "tests" / "fixtures" / "tiny_connections.csv"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
SAVED = REPO_ROOT / "saved_queries"


@pytest.fixture
def loaded(tmp_db_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_db_path)
    db.apply_migrations(conn, REPO_ROOT / "migrations")
    ingest.ingest_csv(conn, FIXTURE_CSV)
    companies.dedupe_companies(conn)
    companies.enrich_companies(conn, load_canned_fiber_stub(FIXTURES_DIR),
                               CostTracker(conn, None), 0.02)
    classify.classify_people(conn, StubClassifierClient(),
                             CostTracker(conn, None),
                             model="claude-haiku-4-5-20251001",
                             batch_size=10,
                             usd_per_input_mtok=1.0, usd_per_output_mtok=5.0)
    views.build_views(conn)
    return conn


@pytest.mark.parametrize("query_file", [
    "reconnect_targets.sql",
    "sales_at_growth_companies.sql",
    "marketing_leaders.sql",
    "founders_in_target_industries.sql",
])
def test_saved_query_parses(loaded, query_file):
    sql = (SAVED / query_file).read_text()
    # must not error; result may be empty on the tiny fixture, that's fine
    rows = loaded.execute(sql).fetchall()
    assert isinstance(rows, list)


def test_metadata_yml_references_saved_queries():
    meta = yaml.safe_load((REPO_ROOT / "metadata.yml").read_text())
    db_meta = meta["databases"]["crm"]
    query_names = set(db_meta["queries"].keys())
    expected = {
        "reconnect_targets", "sales_at_growth_companies",
        "marketing_leaders", "founders_in_target_industries",
    }
    assert expected.issubset(query_names)
```

(Note: this test imports `yaml` — add `pyyaml` to dev deps in pyproject.toml if not already implicit via datasette.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_saved_queries.py -v`
Expected: FAIL — saved_queries files don't exist yet.

- [ ] **Step 3: Write the saved query files**

`saved_queries/reconnect_targets.sql`:
```sql
-- People connected ≥12mo ago who matter for outreach.
SELECT
  first_name, last_name, company_name,
  raw_position, role_bucket, seniority,
  industry, employee_band, funding_stage,
  hq_country,
  connected_on,
  linkedin_url
FROM people_enriched
WHERE connected_on <= date('now', '-12 months')
  AND seniority IN ('Director', 'VP', 'C-suite', 'Founder')
  AND role_bucket IN ('Sales', 'BD', 'Marketing', 'Founder')
ORDER BY connected_on ASC;
```

`saved_queries/sales_at_growth_companies.sql`:
```sql
-- Sales people at Series A/B/C companies in the 51-500 employee band.
SELECT
  first_name, last_name, company_name, raw_position,
  seniority, industry, employee_band, funding_stage,
  hq_country, linkedin_url
FROM people_enriched
WHERE role_bucket = 'Sales'
  AND funding_stage IN ('Series A', 'Series B', 'Series C+')
  AND employee_band IN ('51-200', '201-500')
ORDER BY company_name, seniority;
```

`saved_queries/marketing_leaders.sql`:
```sql
-- Senior marketing leaders worth knowing.
SELECT
  first_name, last_name, company_name, raw_position,
  seniority, industry, employee_band, hq_country, linkedin_url
FROM people_enriched
WHERE role_bucket = 'Marketing'
  AND seniority IN ('VP', 'C-suite')
ORDER BY industry, company_name;
```

`saved_queries/founders_in_target_industries.sql`:
```sql
-- Edit the industry list to match your current business focus.
SELECT
  first_name, last_name, company_name, raw_position,
  industry, employee_band, funding_stage, hq_country, linkedin_url
FROM people_enriched
WHERE role_bucket = 'Founder'
  AND industry IN ('Software', 'Technology', 'AI Tooling')
ORDER BY company_name;
```

- [ ] **Step 4: Write the Datasette metadata**

`metadata.yml`:
```yaml
title: Personal Network CRM
description: Enriched LinkedIn connections — queryable Rolodex 2.0

databases:
  crm:
    title: Network
    queries:
      reconnect_targets:
        sql_file: saved_queries/reconnect_targets.sql
        title: Reconnect targets
        description: Connected ≥12mo ago, senior, in target roles
      sales_at_growth_companies:
        sql_file: saved_queries/sales_at_growth_companies.sql
        title: Sales at growth companies
      marketing_leaders:
        sql_file: saved_queries/marketing_leaders.sql
        title: Marketing leaders
      founders_in_target_industries:
        sql_file: saved_queries/founders_in_target_industries.sql
        title: Founders in target industries
```

- [ ] **Step 5: Add pyyaml to dev deps**

Modify `pyproject.toml`:
```toml
[project.optional-dependencies]
dev = [
  "pytest>=8",
  "respx>=0.21",
  "pytest-asyncio>=0.23",
  "pyyaml>=6",
]
```

Re-install: `pip install -e ".[dev]"`

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_saved_queries.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 7: Commit**

```bash
git add saved_queries/ metadata.yml tests/test_saved_queries.py pyproject.toml
git commit -m "feat(queries): starter saved queries + datasette metadata"
```

---

## Task 16: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README**

`README.md`:
```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README with install, first-run, re-run, and cost guidance"
```

---

## Task 17: Full-suite green + final commit

**Files:** none new

- [ ] **Step 1: Run the full test suite**

```bash
pytest -v
```

Expected: All tests pass. If anything fails: do NOT mask the failure with `try/except`; fix the underlying issue. Re-read the related task to find the discrepancy.

- [ ] **Step 2: Verify the CLI works end-to-end against the fixture**

```bash
NETCRM_TEST_MODE=1 NETCRM_DB_PATH=/tmp/smoke.db netcrm run-all tests/fixtures/tiny_connections.csv
NETCRM_DB_PATH=/tmp/smoke.db netcrm cost-report
```

Expected: `run-all complete`, then the cost report prints per-provider USD.

- [ ] **Step 3: Confirm Datasette renders** (manual sanity)

```bash
NETCRM_DB_PATH=/tmp/smoke.db datasette serve /tmp/smoke.db --metadata metadata.yml
```

Open <http://localhost:8001/crm>. Verify: the four saved queries appear; clicking "reconnect_targets" runs without SQL errors; the `people_enriched` table renders.

- [ ] **Step 4: Tag v0.1**

```bash
git tag -a v0.1.0 -m "v0.1.0 — initial queryable dataset pipeline"
```

(No push — local tag only. Push later if you want.)

---

## Task 18: Real-data first run (optional but recommended)

**Files:** none new

This task is the actual delivery. Do NOT skip the cost estimate.

- [ ] **Step 1: Copy your real CSV in**

```bash
cp ~/Downloads/connections.csv ./connections.csv
```

- [ ] **Step 2: Ingest + dedupe (free)**

```bash
unset NETCRM_TEST_MODE
netcrm ingest ./connections.csv
netcrm dedupe-companies
```

Expected: `ingested N people rows`, then `companies table now has M rows`. Note both numbers.

- [ ] **Step 3: Get a real cost estimate**

```bash
netcrm enrich-companies --dry-run
netcrm classify-people --dry-run
```

Expected: two cost estimates. Sanity-check them. If Fiber estimate is > $200, stop and reconsider (your unique-company count may be higher than expected, or `FIBER_USD_PER_CREDIT` may be misconfigured).

- [ ] **Step 4: Run with a hard cap**

```bash
netcrm enrich-companies --max-spend-usd 100
netcrm classify-people --max-spend-usd 5
netcrm build-views
netcrm cost-report
```

Expected: actual spend within caps. The `cost-report` total roughly matches the dry-run estimate.

- [ ] **Step 5: Browse + verify**

```bash
netcrm serve
```

Verify the four saved queries return sensible results on your real data. If `reconnect_targets` returns zero rows, the most likely cause is that the `connected_on` clause filtered everything; tweak it and re-run.

- [ ] **Step 6: Commit a `.gitignore` exclusion for the real DB**

The `.gitignore` already excludes `crm.db`. Confirm `git status` shows no untracked DB or `.env` files.

---

## Spec coverage check (planning self-audit)

| Spec section | Tasks |
|---|---|
| Architecture (staged + cached pipeline) | 2, 7, 10, 12, 13 |
| Project layout | 1 |
| Data model (people / companies / people_class / costs) | 3 |
| Company-name normalization rules | 5 |
| Classification taxonomy (role_bucket + seniority) | 11 |
| Fiber enrichment + per-row outcomes | 9, 10 |
| Cost guardrails (dry-run, spend cap, log) | 8, 14 |
| Error handling (transient retry-on-next-run) | 9, 10 |
| Schema migrations (linear, additive) | 2 |
| CSV header validation | 6 |
| Testing (unit + fixtures + smoke) | 4, every test_*.py |
| Live integration test (gated by RUN_LIVE_TESTS) | 9 (step 5) |
| Saved queries + metadata | 15 |
| CLI shape (ingest / dedupe / enrich / classify / views / run-all / serve / cost-report) | 14 |
| README (install, first-run, re-run, costs) | 16 |
| Acceptance criteria (run-all green on real data, re-runs free) | 17, 18 |
