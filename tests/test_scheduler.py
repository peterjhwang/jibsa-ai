"""Tests for ReminderScheduler."""
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from src.scheduler import ReminderScheduler


@pytest.fixture
def mock_slack():
    return MagicMock()


@pytest.fixture
def scheduler(mock_slack, tmp_path):
    s = ReminderScheduler(mock_slack, timezone="UTC", db_path=str(tmp_path / "test.db"), persist=False)
    s.start()
    yield s
    s.shutdown()


def test_add_reminder_in_future(scheduler):
    run_at = datetime.now(ZoneInfo("UTC")) + timedelta(hours=1)
    result = scheduler.add_reminder("C123", "ts-1", "Test reminder", run_at)
    assert result["ok"] is True
    assert "job_id" in result


def test_add_reminder_in_past_rejected(scheduler):
    run_at = datetime.now(ZoneInfo("UTC")) - timedelta(hours=1)
    result = scheduler.add_reminder("C123", "ts-1", "Past reminder", run_at)
    assert result["ok"] is False
    assert "future" in result["error"]


def test_list_reminders(scheduler):
    run_at = datetime.now(ZoneInfo("UTC")) + timedelta(hours=1)
    scheduler.add_reminder("C123", "ts-1", "Reminder 1", run_at)
    scheduler.add_reminder("C123", "ts-2", "Reminder 2", run_at)
    reminders = scheduler.list_reminders()
    assert len(reminders) == 2


def test_cancel_reminder(scheduler):
    run_at = datetime.now(ZoneInfo("UTC")) + timedelta(hours=1)
    result = scheduler.add_reminder("C123", "ts-1", "To cancel", run_at)
    job_id = result["job_id"]
    cancel_result = scheduler.cancel_reminder(job_id)
    assert cancel_result["ok"] is True
    assert len(scheduler.list_reminders()) == 0


def test_cancel_nonexistent_reminder(scheduler):
    result = scheduler.cancel_reminder("fake-id")
    assert result["ok"] is False


def test_reminder_fires_and_posts(scheduler, mock_slack):
    """Reminder should fire and post to Slack."""
    run_at = datetime.now(ZoneInfo("UTC")) + timedelta(seconds=1)
    scheduler.add_reminder("C123", "ts-1", "Fire test", run_at)
    time.sleep(2)
    mock_slack.chat_postMessage.assert_called_once()
    call_kwargs = mock_slack.chat_postMessage.call_args.kwargs
    assert "Fire test" in call_kwargs["text"]
    assert call_kwargs["channel"] == "C123"


# ---------------------------------------------------------------------------
# Time parser tests
# ---------------------------------------------------------------------------

def test_parse_iso_format():
    from src.orchestrator import _parse_reminder_time
    result = _parse_reminder_time("2026-03-14T09:00:00", "UTC")
    assert result is not None
    assert result.hour == 9


def test_parse_relative_minutes():
    from src.orchestrator import _parse_reminder_time
    result = _parse_reminder_time("in 30 minutes", "UTC")
    assert result is not None
    assert result > datetime.now(ZoneInfo("UTC"))


def test_parse_relative_hours():
    from src.orchestrator import _parse_reminder_time
    result = _parse_reminder_time("in 2 hours", "UTC")
    assert result is not None


def test_parse_tomorrow():
    from src.orchestrator import _parse_reminder_time
    result = _parse_reminder_time("tomorrow at 9am", "UTC")
    assert result is not None
    assert result.hour == 9


def test_parse_invalid_returns_none():
    from src.orchestrator import _parse_reminder_time
    result = _parse_reminder_time("next blue moon", "UTC")
    assert result is None
