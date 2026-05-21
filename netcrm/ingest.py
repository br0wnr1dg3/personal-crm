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

    try:
        header_idx = _find_header_row(lines)
    except ValueError:
        # Not a LinkedIn CSV; try to read first line to report header mismatch
        peek_reader = csv.DictReader([lines[0]] if lines else [])
        raise ValueError(
            f"unexpected CSV headers: got {peek_reader.fieldnames!r}, "
            f"expected {EXPECTED_HEADERS!r}"
        )

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
