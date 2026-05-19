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
