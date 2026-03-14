"""
Scheduler — APScheduler wrapper for timed actions (reminders, recurring tasks).

Jobs are persisted in SQLite via SQLAlchemyJobStore so reminders survive restarts.
Reminder metadata (channel, thread, created_by) is stored in a separate SQLite table
since APScheduler's job store only keeps the callable + trigger.
"""
import logging
import sqlite3
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data/jibsa.db"


class ReminderScheduler:
    """Manages scheduled reminders backed by APScheduler + SQLite persistence."""

    def __init__(
        self,
        slack_client: Any,
        timezone: str = "UTC",
        db_path: str | None = None,
        persist: bool = True,
    ):
        self._slack = slack_client
        self._timezone = timezone
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._lock = threading.Lock()

        # Ensure parent dir exists
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        # APScheduler with SQLite job store for persistence across restarts.
        jobstores = {}
        if persist:
            jobstores["default"] = SQLAlchemyJobStore(url=f"sqlite:///{self._db_path}")

        self._scheduler = BackgroundScheduler(
            jobstores=jobstores,
            timezone=timezone,
        )

        # Separate metadata table (channel, thread, message, created_by)
        # APScheduler only stores the callable — we need the human-readable info
        self._meta_conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._meta_conn.row_factory = sqlite3.Row
        self._init_meta_table()

        self._started = False

    def _init_meta_table(self) -> None:
        with self._lock:
            self._meta_conn.execute("""
                CREATE TABLE IF NOT EXISTS reminder_metadata (
                    job_id      TEXT PRIMARY KEY,
                    channel     TEXT NOT NULL,
                    thread_ts   TEXT NOT NULL,
                    message     TEXT NOT NULL,
                    run_at      TEXT NOT NULL,
                    created_by  TEXT NOT NULL DEFAULT ''
                )
            """)
            self._meta_conn.commit()

    def start(self) -> None:
        """Start the scheduler. Safe to call multiple times."""
        if not self._started:
            self._scheduler.start()
            self._started = True
            # Clean up metadata for jobs that no longer exist (fired while we were down)
            self._cleanup_stale_metadata()
            logger.info("ReminderScheduler started (tz=%s, db=%s)", self._timezone, self._db_path)

    def shutdown(self) -> None:
        """Shut down the scheduler gracefully."""
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
        self._meta_conn.close()

    def add_reminder(
        self,
        channel: str,
        thread_ts: str,
        message: str,
        run_at: datetime,
        created_by: str = "",
    ) -> dict:
        """Schedule a reminder. Returns {"ok": True, "job_id": str, "run_at": str}."""
        if run_at <= datetime.now(run_at.tzinfo):
            return {"ok": False, "error": "Reminder time must be in the future"}

        job = self._scheduler.add_job(
            self._fire_reminder,
            trigger="date",
            run_date=run_at,
            kwargs={
                "channel": channel,
                "thread_ts": thread_ts,
                "message": message,
            },
        )

        # Store metadata
        with self._lock:
            self._meta_conn.execute(
                """
                INSERT OR REPLACE INTO reminder_metadata (job_id, channel, thread_ts, message, run_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job.id, channel, thread_ts, message, run_at.isoformat(), created_by),
            )
            self._meta_conn.commit()

        logger.info("Reminder scheduled: job=%s at=%s msg=%.60s", job.id, run_at, message)
        return {"ok": True, "job_id": job.id, "run_at": run_at.isoformat()}

    def cancel_reminder(self, job_id: str) -> dict:
        """Cancel a scheduled reminder."""
        # Check metadata first
        row = self._meta_conn.execute(
            "SELECT job_id FROM reminder_metadata WHERE job_id = ?", (job_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"No reminder with ID '{job_id}'"}
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass  # Job may have already fired
        with self._lock:
            self._meta_conn.execute("DELETE FROM reminder_metadata WHERE job_id = ?", (job_id,))
            self._meta_conn.commit()
        return {"ok": True}

    def list_reminders(self) -> list[dict]:
        """Return all pending reminders with metadata."""
        rows = self._meta_conn.execute(
            "SELECT * FROM reminder_metadata ORDER BY run_at"
        ).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "channel": row["channel"],
                "thread_ts": row["thread_ts"],
                "message": row["message"],
                "run_at": row["run_at"],
                "created_by": row["created_by"],
            }
            for row in rows
        ]

    def _fire_reminder(self, channel: str, thread_ts: str, message: str) -> None:
        """Called by APScheduler when a reminder fires."""
        try:
            self._slack.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"\u23f0 *Reminder:* {message}",
            )
            logger.info("Reminder fired: channel=%s msg=%.60s", channel, message)
        except Exception as e:
            logger.error("Failed to post reminder: %s", e)
        finally:
            # Clean up metadata for fired reminder
            with self._lock:
                self._meta_conn.execute(
                    "DELETE FROM reminder_metadata WHERE channel = ? AND message = ?",
                    (channel, message),
                )
                self._meta_conn.commit()

    def _cleanup_stale_metadata(self) -> None:
        """Remove metadata for jobs that no longer exist in APScheduler."""
        existing_ids = {job.id for job in self._scheduler.get_jobs()}
        rows = self._meta_conn.execute("SELECT job_id FROM reminder_metadata").fetchall()
        stale = [row["job_id"] for row in rows if row["job_id"] not in existing_ids]
        if stale:
            with self._lock:
                for jid in stale:
                    self._meta_conn.execute("DELETE FROM reminder_metadata WHERE job_id = ?", (jid,))
                self._meta_conn.commit()
            logger.info("Cleaned up %d stale reminder metadata entries", len(stale))
