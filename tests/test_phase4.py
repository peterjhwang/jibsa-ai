"""Tests for Phase 4: Google Calendar, Gmail, and scheduled jobs."""
import os
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from src.context import current_user_id


# ---------------------------------------------------------------------------
# Google Calendar Client
# ---------------------------------------------------------------------------

class TestGoogleCalendarClient:
    @pytest.fixture
    def client(self):
        with patch("src.integrations.google_calendar_client.build") as mock_build:
            from src.integrations.google_calendar_client import GoogleCalendarClient
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            c = GoogleCalendarClient(MagicMock())
            c._mock = mock_service
            return c

    def test_list_today_events(self, client):
        client._mock.events.return_value.list.return_value.execute.return_value = {
            "items": [{"summary": "Standup", "start": {"dateTime": "2026-03-14T09:00:00+09:00"}}]
        }
        events = client.list_today_events("Asia/Seoul")
        assert len(events) == 1
        assert events[0]["summary"] == "Standup"

    def test_list_upcoming_events(self, client):
        client._mock.events.return_value.list.return_value.execute.return_value = {
            "items": [{"summary": "Meeting"}, {"summary": "Lunch"}]
        }
        events = client.list_upcoming_events(days=7)
        assert len(events) == 2

    def test_search_events(self, client):
        client._mock.events.return_value.list.return_value.execute.return_value = {"items": []}
        events = client.search_events("team meeting")
        assert events == []

    def test_create_event(self, client):
        client._mock.events.return_value.insert.return_value.execute.return_value = {
            "id": "ev1", "htmlLink": "https://calendar.google.com/event/ev1",
            "summary": "New Meeting",
        }
        event = client.create_event("New Meeting", "2026-03-15T10:00:00", "2026-03-15T11:00:00")
        assert event["id"] == "ev1"

    def test_delete_event(self, client):
        client._mock.events.return_value.delete.return_value.execute.return_value = None
        client.delete_event("ev1")  # should not raise

    def test_execute_step_create(self, client):
        client._mock.events.return_value.insert.return_value.execute.return_value = {
            "id": "ev2", "htmlLink": "https://calendar.google.com/event/ev2",
        }
        step = {
            "action": "create_event",
            "params": {"summary": "Test", "start": "2026-03-15T10:00:00", "end": "2026-03-15T11:00:00"},
            "description": "Create event",
        }
        result = client.execute_step(step)
        assert result["ok"] is True

    def test_execute_step_unknown(self, client):
        step = {"action": "cancel_event", "params": {}, "description": "Cancel"}
        result = client.execute_step(step)
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# Gmail Client
# ---------------------------------------------------------------------------

class TestGmailClient:
    @pytest.fixture
    def client(self):
        with patch("src.integrations.gmail_client.build") as mock_build:
            from src.integrations.gmail_client import GmailClient
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            c = GmailClient(MagicMock())
            c._mock = mock_service
            return c

    def test_list_messages(self, client):
        client._mock.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "msg1"}, {"id": "msg2"}],
        }
        # Mock individual message gets
        def mock_get(**kwargs):
            mock_resp = MagicMock()
            mock_resp.execute.return_value = {
                "id": kwargs.get("id", "msg"),
                "snippet": "Hello world",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Test Email"},
                        {"name": "From", "value": "sender@test.com"},
                        {"name": "Date", "value": "2026-03-14"},
                    ]
                },
            }
            return mock_resp
        client._mock.users.return_value.messages.return_value.get.side_effect = mock_get

        messages = client.list_messages(max_results=2)
        assert len(messages) == 2
        assert messages[0]["subject"] == "Test Email"

    def test_send_message(self, client):
        client._mock.users.return_value.messages.return_value.send.return_value.execute.return_value = {
            "id": "sent1", "labelIds": ["SENT"],
        }
        result = client.send_message("to@test.com", "Subject", "Body")
        assert result["id"] == "sent1"

    def test_execute_step_send(self, client):
        client._mock.users.return_value.messages.return_value.send.return_value.execute.return_value = {
            "id": "sent2",
        }
        step = {
            "action": "send_email",
            "params": {"to": "to@test.com", "subject": "Hi", "body": "Hello"},
            "description": "Send email",
        }
        result = client.execute_step(step)
        assert result["ok"] is True

    def test_execute_step_unknown(self, client):
        step = {"action": "delete_email", "params": {}, "description": "Delete"}
        result = client.execute_step(step)
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# CalendarReadTool
# ---------------------------------------------------------------------------

class TestCalendarReadToolIntegration:
    def test_returns_events_for_connected_user(self):
        from src.tools.calendar_tool import CalendarReadTool
        mock_oauth = MagicMock()
        mock_creds = MagicMock()
        mock_oauth.get_valid_credentials.return_value = mock_creds

        tool = CalendarReadTool.create(mock_oauth, "UTC")

        with patch("src.integrations.google_calendar_client.build") as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.events.return_value.list.return_value.execute.return_value = {
                "items": [{"summary": "Standup", "start": {"dateTime": "2026-03-14T09:00:00Z"}, "end": {"dateTime": "2026-03-14T09:30:00Z"}}],
            }
            token = current_user_id.set("U001")
            result = tool._run("my meetings today")
            current_user_id.reset(token)

        assert "Standup" in result


# ---------------------------------------------------------------------------
# GmailReadTool
# ---------------------------------------------------------------------------

class TestGmailReadToolIntegration:
    def test_returns_messages_for_connected_user(self):
        from src.tools.gmail_tool import GmailReadTool
        mock_oauth = MagicMock()
        mock_creds = MagicMock()
        mock_oauth.get_valid_credentials.return_value = mock_creds

        tool = GmailReadTool.create(mock_oauth)

        with patch("src.integrations.gmail_client.build") as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
                "messages": [{"id": "msg1"}],
            }
            mock_service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
                "id": "msg1", "snippet": "Your invoice",
                "payload": {"headers": [
                    {"name": "Subject", "value": "Invoice"},
                    {"name": "From", "value": "billing@test.com"},
                    {"name": "Date", "value": "2026-03-14"},
                ]},
            }
            token = current_user_id.set("U001")
            result = tool._run("unread emails")
            current_user_id.reset(token)

        assert "Invoice" in result


# ---------------------------------------------------------------------------
# Orchestrator execute_plan dispatch
# ---------------------------------------------------------------------------

class TestOrchestratorGoogleDispatch:
    @pytest.fixture
    def orch(self, tmp_path):
        from src.orchestrator import Orchestrator
        config = {
            "jibsa": {
                "max_history": 20, "claude_timeout": 120, "timezone": "UTC",
                "intern_db_path": str(tmp_path / "interns.db"),
                "credential_db_path": str(tmp_path / "creds.db"),
            },
            "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            "approval": {"approve_keywords": ["yes"], "reject_keywords": ["no"]},
            "integrations": {},
        }
        env = {"SLACK_BOT_TOKEN": "xoxb-test", "ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict(os.environ, env), \
             patch("src.orchestrator.CrewRunner") as MockRunner:
            MockRunner.return_value = MagicMock()
            o = Orchestrator(MagicMock(), config)
            return o

    def test_calendar_step_no_user(self, orch):
        token = current_user_id.set("")
        result = orch._execute_google_step(
            {"action": "create_event", "params": {}, "description": "Create"},
            "calendar",
        )
        current_user_id.reset(token)
        assert result["ok"] is False
        assert "Could not determine" in result["error"]

    def test_calendar_step_not_connected(self, orch):
        token = current_user_id.set("U999")
        result = orch._execute_google_step(
            {"action": "create_event", "params": {}, "description": "Create"},
            "calendar",
        )
        current_user_id.reset(token)
        assert result["ok"] is False
        assert "not connected" in result["error"].lower()


# ---------------------------------------------------------------------------
# CredentialStore.list_users_for_service
# ---------------------------------------------------------------------------

class TestListUsersForService:
    def test_list_users(self, tmp_path):
        from src.integrations.credential_store import CredentialStore
        key = Fernet.generate_key().decode()
        store = CredentialStore(db_path=str(tmp_path / "test.db"), encryption_key=key)
        store.set("U001", "google", {"token": "a"})
        store.set("U002", "google", {"token": "b"})
        store.set("U003", "github", {"token": "c"})

        users = store.list_users_for_service("google")
        assert sorted(users) == ["U001", "U002"]
        assert store.list_users_for_service("github") == ["U003"]
        assert store.list_users_for_service("nonexistent") == []
        store.close()


# ---------------------------------------------------------------------------
# VALID_TOOL_NAMES
# ---------------------------------------------------------------------------

class TestGmailInValidTools:
    def test_gmail_is_valid(self):
        from src.models.intern import VALID_TOOL_NAMES
        assert "gmail" in VALID_TOOL_NAMES
        assert "calendar" in VALID_TOOL_NAMES
