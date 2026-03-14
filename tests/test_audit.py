"""Tests for AuditStore and @jibsa audit command."""
import os
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.audit_store import AuditStore


class TestAuditStore:
    @pytest.fixture
    def store(self, tmp_path):
        s = AuditStore(db_path=str(tmp_path / "audit.db"))
        yield s
        s.close()

    def test_log_and_query(self, store):
        store.log(action="plan_approved", user_id="U001", details={"summary": "Create task"})
        entries = store.query(limit=10)
        assert len(entries) == 1
        assert entries[0]["action"] == "plan_approved"
        assert entries[0]["user_id"] == "U001"
        assert entries[0]["details"]["summary"] == "Create task"

    def test_query_empty(self, store):
        assert store.query() == []

    def test_query_limit(self, store):
        for i in range(10):
            store.log(action=f"action_{i}")
        entries = store.query(limit=3)
        assert len(entries) == 3

    def test_query_newest_first(self, store):
        store.log(action="first")
        store.log(action="second")
        entries = store.query(limit=10)
        assert entries[0]["action"] == "second"
        assert entries[1]["action"] == "first"

    def test_query_action_filter(self, store):
        store.log(action="plan_approved")
        store.log(action="plan_rejected")
        store.log(action="plan_approved")

        approved = store.query(action_filter="plan_approved")
        assert len(approved) == 2
        assert all(e["action"] == "plan_approved" for e in approved)

    def test_all_fields_stored(self, store):
        store.log(
            action="plan_executed",
            user_id="U002",
            service="notion",
            details={"summary": "Update task", "steps": 2},
            status="partial",
            thread_ts="ts-123",
        )
        entry = store.query(limit=1)[0]
        assert entry["user_id"] == "U002"
        assert entry["service"] == "notion"
        assert entry["status"] == "partial"
        assert entry["thread_ts"] == "ts-123"
        assert entry["details"]["steps"] == 2

    def test_timestamp_populated(self, store):
        store.log(action="test")
        entry = store.query(limit=1)[0]
        assert entry["timestamp"]  # should be non-empty datetime string


class TestAuditCommand:
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
             patch("src.orchestrator.CrewRunner") as MockRunner, \
             patch("src.orchestrator.build_second_brain", return_value=None):
            MockRunner.return_value = MagicMock()
            mock_slack = MagicMock()
            mock_slack.chat_postMessage.return_value = {"ts": "t1"}
            o = Orchestrator(mock_slack, config)
            o.runner = MockRunner.return_value
            return o

    def test_audit_command_empty(self, orch):
        orch.handle_message("C123", "ts-1", "U001", "audit")
        text = orch.slack.chat_postMessage.call_args.kwargs["text"]
        assert "No audit entries" in text

    def test_audit_command_shows_entries(self, orch):
        orch.audit.log(action="plan_approved", user_id="U001", details={"summary": "Test"})
        orch.audit.log(action="intern_deactivated", user_id="U002", details={"name": "Alex"})
        orch.handle_message("C123", "ts-2", "U001", "audit")
        call_kwargs = orch.slack.chat_postMessage.call_args.kwargs
        assert "blocks" in call_kwargs

    def test_plan_execution_creates_audit_entry(self, orch):
        from src.context import current_user_id
        token = current_user_id.set("U001")
        plan = {"summary": "Test plan", "steps": []}
        orch._execute_plan(plan, "C123", "ts-3")
        current_user_id.reset(token)
        entries = orch.audit.query(action_filter="plan_executed")
        assert len(entries) == 1
        assert entries[0]["details"]["summary"] == "Test plan"
