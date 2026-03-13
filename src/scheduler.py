"""
Scheduler — APScheduler wrapper for timed actions (reminders, recurring tasks).

Provides a singleton-style scheduler that the orchestrator initializes at startup.
Reminders fire as Slack messages posted to the originating channel/thread.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


class ReminderScheduler:
    """Manages scheduled reminders backed by APScheduler."""

    def __init__(self, slack_client: Any, timezone: str = "UTC"):
        self._slack = slack_client
        self._timezone = timezone
        self._scheduler = BackgroundScheduler(timezone=timezone)
        self._reminders: dict[str, dict] = {}  # job_id → metadata
        self._started = False

    def start(self) -> None:
        """Start the scheduler. Safe to call multiple times."""
        if not self._started:
            self._scheduler.start()
            self._started = True
            logger.info("ReminderScheduler started (tz=%s)", self._timezone)

    def shutdown(self) -> None:
        """Shut down the scheduler gracefully."""
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False

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

        self._reminders[job.id] = {
            "channel": channel,
            "thread_ts": thread_ts,
            "message": message,
            "run_at": run_at.isoformat(),
            "created_by": created_by,
        }

        logger.info("Reminder scheduled: job=%s at=%s msg=%.60s", job.id, run_at, message)
        return {"ok": True, "job_id": job.id, "run_at": run_at.isoformat()}

    def cancel_reminder(self, job_id: str) -> dict:
        """Cancel a scheduled reminder."""
        if job_id not in self._reminders:
            return {"ok": False, "error": f"No reminder with ID '{job_id}'"}
        try:
            self._scheduler.remove_job(job_id)
            del self._reminders[job_id]
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def list_reminders(self) -> list[dict]:
        """Return all pending reminders."""
        return [
            {"job_id": jid, **meta}
            for jid, meta in self._reminders.items()
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
            # Clean up metadata for fired reminders
            for jid, meta in list(self._reminders.items()):
                if meta["channel"] == channel and meta["message"] == message:
                    self._reminders.pop(jid, None)
                    break
