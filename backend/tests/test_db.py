from pathlib import Path

from app.db import Database


def test_database_initializes_core_tables(tmp_path: Path):
    db = Database(tmp_path / "app.sqlite3")
    db.initialize()

    db.set_setting("download_dir", "/tmp/downloads")

    assert db.get_setting("download_dir") == "/tmp/downloads"
    assert db.query_one("SELECT COUNT(*) AS count FROM tasks")["count"] == 0
