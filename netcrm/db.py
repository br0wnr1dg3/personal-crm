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
