"""
ScheduleStore — SQLite backend for user-configurable schedules.

Stores recurring schedule definitions (built-in job timings + custom recurring
reminders) in the same jibsa.db alongside interns and SOPs.
"""
import logging
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data/jibsa.db"


class ScheduleStore:
    """SQLite CRUD for user schedules."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._lock = threading.Lock()

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_table()

    def _init_table(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS user_schedules (
                    id            TEXT PRIMARY KEY,
                    user_id       TEXT NOT NULL,
                    name          TEXT NOT NULL,
                    schedule_type TEXT NOT NULL,
                    cron          TEXT NOT NULL,
                    message       TEXT DEFAULT '',
                    channel       TEXT NOT NULL,
                    enabled       BOOLEAN DEFAULT 1,
                    created_at    TEXT NOT NULL
                )
            """)
            self._conn.commit()

    def add(
        self,
        user_id: str,
        name: str,
        schedule_type: str,
        cron: str,
        channel: str,
        message: str = "",
    ) -> dict:
        """Create a schedule. Returns the full schedule dict."""
        schedule_id = str(uuid.uuid4())[:8]
        now = datetime.now(tz=None).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO user_schedules (id, user_id, name, schedule_type, cron, message, channel, enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (schedule_id, user_id, name, schedule_type, cron, message, channel, now),
            )
            self._conn.commit()
        logger.info("Schedule created: id=%s name=%s cron=%s user=%s", schedule_id, name, cron, user_id)
        return {
            "id": schedule_id,
            "user_id": user_id,
            "name": name,
            "schedule_type": schedule_type,
            "cron": cron,
            "message": message,
            "channel": channel,
            "enabled": True,
            "created_at": now,
        }

    def remove(self, schedule_id: str) -> bool:
        """Delete a schedule by ID. Returns True if deleted."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM user_schedules WHERE id = ?", (schedule_id,)
            )
            self._conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Schedule deleted: id=%s", schedule_id)
        return deleted

    def remove_by_user_and_name(self, user_id: str, name: str) -> str | None:
        """Delete a schedule by user + name. Returns the schedule ID if deleted, else None."""
        row = self._conn.execute(
            "SELECT id FROM user_schedules WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()
        if not row:
            return None
        schedule_id = row["id"]
        self.remove(schedule_id)
        return schedule_id

    def list_for_user(self, user_id: str) -> list[dict]:
        """Return all schedules for a user."""
        rows = self._conn.execute(
            "SELECT * FROM user_schedules WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all_enabled(self) -> list[dict]:
        """Return all enabled schedules (for startup re-registration)."""
        rows = self._conn.execute(
            "SELECT * FROM user_schedules WHERE enabled = 1 ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def get(self, schedule_id: str) -> dict | None:
        """Get a single schedule by ID."""
        row = self._conn.execute(
            "SELECT * FROM user_schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        return dict(row) if row else None
