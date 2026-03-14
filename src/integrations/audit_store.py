"""
AuditStore — persistent audit logging backed by SQLite.

Logs all significant actions (plan proposals, approvals, executions,
intern CRUD, connections, scheduled jobs) to the audit_log table.
"""
import json
import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data/jibsa.db"


class AuditStore:
    """SQLite-backed audit log."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._lock = threading.Lock()

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
                    user_id     TEXT NOT NULL DEFAULT '',
                    action      TEXT NOT NULL,
                    service     TEXT NOT NULL DEFAULT '',
                    details     TEXT NOT NULL DEFAULT '{}',
                    status      TEXT NOT NULL DEFAULT 'ok',
                    thread_ts   TEXT NOT NULL DEFAULT ''
                )
            """)
            self._conn.commit()

    def log(
        self,
        action: str,
        user_id: str = "",
        service: str = "",
        details: dict | None = None,
        status: str = "ok",
        thread_ts: str = "",
    ) -> None:
        """Write an audit log entry."""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO audit_log (user_id, action, service, details, status, thread_ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    action,
                    service,
                    json.dumps(details or {}),
                    status,
                    thread_ts,
                ),
            )
            self._conn.commit()

    def query(self, limit: int = 20, action_filter: str | None = None) -> list[dict]:
        """Return recent audit entries, newest first."""
        sql = "SELECT * FROM audit_log"
        params: list = []
        if action_filter:
            sql += " WHERE action = ?"
            params.append(action_filter)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("details"), str):
                try:
                    d["details"] = json.loads(d["details"])
                except (json.JSONDecodeError, TypeError):
                    pass
            results.append(d)
        return results

    def close(self) -> None:
        self._conn.close()
