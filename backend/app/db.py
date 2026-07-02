from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .utils import now_iso


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compliance_consent (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    accepted INTEGER NOT NULL DEFAULT 0,
    version TEXT NOT NULL,
    accepted_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'queued',
    message TEXT,
    output_dir TEXT NOT NULL,
    output_path TEXT,
    options_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    message TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(sql, tuple(params))
            conn.commit()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(sql, tuple(params)).fetchone()
            return dict(row) if row else None

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]

    def claim_next_queued_task(self) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM tasks WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            task_id = row["id"]
            timestamp = now_iso()
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'running',
                    stage = 'preparing',
                    progress = 1,
                    message = '准备下载',
                    updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (timestamp, task_id),
            )
            if cursor.rowcount != 1:
                conn.commit()
                return None
            claimed = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            conn.commit()
            return dict(claimed) if claimed else None

    def get_setting(self, key: str) -> str | None:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, now_iso()),
        )

    def delete_setting(self, key: str) -> None:
        self.execute("DELETE FROM settings WHERE key = ?", (key,))

    def get_json_setting(self, key: str, default: Any = None) -> Any:
        value = self.get_setting(key)
        if value is None:
            return default
        return json.loads(value)

    def set_json_setting(self, key: str, value: Any) -> None:
        self.set_setting(key, json.dumps(value, ensure_ascii=False))
