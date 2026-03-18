"""Tests for user-configurable schedules."""
import pytest
from unittest.mock import MagicMock, patch

from src.integrations.schedule_store import ScheduleStore
from src.orchestrator import _parse_schedule_spec


# ---------------------------------------------------------------------------
# _parse_schedule_spec
# ---------------------------------------------------------------------------


class TestParseScheduleSpec:
    def test_time_with_weekdays(self):
        result = _parse_schedule_spec("8:30am weekdays")
        assert result is not None
        cron, desc = result
        assert cron == "30 8 * * 1-5"
        assert "8:30" in desc
        assert "weekdays" in desc

    def test_pm_time_weekdays(self):
        cron, _ = _parse_schedule_spec("5pm weekdays")
        assert cron == "0 17 * * 1-5"

    def test_day_name(self):
        cron, desc = _parse_schedule_spec("friday 4pm")
        assert cron == "0 16 * * 4"
        assert "Friday" in desc

    def test_every_day(self):
        cron, desc = _parse_schedule_spec("every day 9am")
        assert cron == "0 9 * * *"
        assert "daily" in desc

    def test_every_weekday(self):
        cron, _ = _parse_schedule_spec("every weekday 8:30am")
        assert cron == "30 8 * * 1-5"

    def test_daily_shorthand(self):
        cron, _ = _parse_schedule_spec("9am daily")
        assert cron == "0 9 * * *"

    def test_bare_time_defaults_daily(self):
        cron, _ = _parse_schedule_spec("6pm")
        assert cron == "0 18 * * *"

    def test_invalid_returns_none(self):
        assert _parse_schedule_spec("whenever you feel like it") is None

    def test_no_time_returns_none(self):
        assert _parse_schedule_spec("weekdays") is None

    def test_24h_time(self):
        cron, _ = _parse_schedule_spec("17:00 weekdays")
        assert cron == "0 17 * * 1-5"

    def test_monday_morning(self):
        cron, _ = _parse_schedule_spec("monday 10:30am")
        assert "30 10" in cron


# ---------------------------------------------------------------------------
# ScheduleStore
# ---------------------------------------------------------------------------


class TestScheduleStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ScheduleStore(db_path=str(tmp_path / "test.db"))

    def test_add_and_list(self, store):
        sched = store.add("U123", "morning_briefing", "builtin", "30 8 * * 1-5", "C001")
        assert sched["id"]
        assert sched["name"] == "morning_briefing"

        schedules = store.list_for_user("U123")
        assert len(schedules) == 1
        assert schedules[0]["cron"] == "30 8 * * 1-5"

    def test_list_empty_user(self, store):
        assert store.list_for_user("U999") == []

    def test_remove(self, store):
        sched = store.add("U123", "test", "custom", "0 9 * * *", "C001", message="hello")
        assert store.remove(sched["id"]) is True
        assert store.list_for_user("U123") == []

    def test_remove_nonexistent(self, store):
        assert store.remove("fake-id") is False

    def test_remove_by_user_and_name(self, store):
        store.add("U123", "morning_briefing", "builtin", "30 8 * * 1-5", "C001")
        removed_id = store.remove_by_user_and_name("U123", "morning_briefing")
        assert removed_id is not None
        assert store.list_for_user("U123") == []

    def test_remove_by_user_and_name_not_found(self, store):
        assert store.remove_by_user_and_name("U123", "nonexistent") is None

    def test_list_all_enabled(self, store):
        store.add("U1", "job1", "builtin", "0 8 * * *", "C001")
        store.add("U2", "job2", "custom", "0 9 * * 1", "C002", message="check")
        all_enabled = store.list_all_enabled()
        assert len(all_enabled) == 2

    def test_get(self, store):
        sched = store.add("U123", "test", "custom", "0 9 * * *", "C001", message="hi")
        fetched = store.get(sched["id"])
        assert fetched is not None
        assert fetched["name"] == "test"

    def test_get_nonexistent(self, store):
        assert store.get("fake-id") is None

    def test_multiple_users_isolated(self, store):
        store.add("U1", "job1", "builtin", "0 8 * * *", "C001")
        store.add("U2", "job2", "builtin", "0 9 * * *", "C002")
        assert len(store.list_for_user("U1")) == 1
        assert len(store.list_for_user("U2")) == 1

    def test_replacing_schedule_by_name(self, store):
        store.add("U1", "morning_briefing", "builtin", "0 8 * * 1-5", "C001")
        store.remove_by_user_and_name("U1", "morning_briefing")
        store.add("U1", "morning_briefing", "builtin", "30 8 * * 1-5", "C001")
        schedules = store.list_for_user("U1")
        assert len(schedules) == 1
        assert schedules[0]["cron"] == "30 8 * * 1-5"
