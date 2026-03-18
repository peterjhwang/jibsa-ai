"""Tests for intern templates — pre-built JD library."""
import os
from unittest.mock import MagicMock, patch

import pytest

from src.intern_templates import list_templates, get_template, template_to_jd, TEMPLATES


class TestTemplateLibrary:
    def test_all_templates_present(self):
        assert len(TEMPLATES) == 5
        assert "content" in TEMPLATES
        assert "sales-ops" in TEMPLATES
        assert "support" in TEMPLATES
        assert "standup" in TEMPLATES
        assert "metrics" in TEMPLATES

    def test_list_templates(self):
        result = list_templates()
        assert len(result) == 5
        assert all("key" in t and "name" in t and "role" in t for t in result)

    def test_get_template_by_key(self):
        tmpl = get_template("content")
        assert tmpl is not None
        assert tmpl["role"] == "Content Marketing Intern"

    def test_get_template_by_name(self):
        tmpl = get_template("Support")
        assert tmpl is not None
        assert "Triage" in tmpl["role"]

    def test_get_template_by_role_keyword(self):
        tmpl = get_template("standup")
        assert tmpl is not None
        assert "Standup" in tmpl["role"]

    def test_get_template_case_insensitive(self):
        assert get_template("CONTENT") is not None
        assert get_template("Sales-Ops") is not None

    def test_get_template_unknown(self):
        assert get_template("nonexistent") is None

    def test_template_to_jd(self):
        tmpl = TEMPLATES["content"]
        jd = template_to_jd(tmpl, created_by="U001")
        assert jd.name == "Content"
        assert jd.role == "Content Marketing Intern"
        assert len(jd.responsibilities) == 5
        assert jd.created_by == "U001"
        assert jd.validate() == []  # should be valid

    def test_all_templates_produce_valid_jds(self):
        for key, tmpl in TEMPLATES.items():
            jd = template_to_jd(tmpl)
            errors = jd.validate()
            assert errors == [], f"Template '{key}' has validation errors: {errors}"

    def test_all_template_tools_are_valid(self):
        from src.models.intern import VALID_TOOL_NAMES
        for key, tmpl in TEMPLATES.items():
            for tool in tmpl["tools_allowed"]:
                assert tool in VALID_TOOL_NAMES, f"Template '{key}' has invalid tool: {tool}"


class TestTemplateCommands:
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
            mock_slack = MagicMock()
            mock_slack.chat_postMessage.return_value = {"ts": "t1"}
            o = Orchestrator(mock_slack, config)
            o.runner = MockRunner.return_value
            return o

    def test_templates_command(self, orch):
        orch.handle_message("C123", "ts-1", "U001", "templates")
        call_kwargs = orch.slack.chat_postMessage.call_args.kwargs
        assert "blocks" in call_kwargs
        # Should list all 5 templates
        text = call_kwargs.get("text", "")
        assert "Content" in text
        assert "Support" in text

    def test_hire_from_template_success(self, orch):
        orch.handle_message("C123", "ts-2", "U001", "hire from template content")
        text = orch.slack.chat_postMessage.call_args.kwargs["text"]
        assert "Content" in text
        assert "ready" in text.lower()
        # Verify intern was created
        assert orch.intern_registry.get_intern("Content") is not None

    def test_hire_from_template_unknown(self, orch):
        orch.handle_message("C123", "ts-3", "U001", "hire from template nonexistent")
        text = orch.slack.chat_postMessage.call_args.kwargs["text"]
        assert "No template" in text

    def test_hire_from_template_duplicate(self, orch):
        orch.handle_message("C123", "ts-4", "U001", "hire from template content")
        orch.slack.reset_mock()
        orch.handle_message("C123", "ts-5", "U001", "hire from template content")
        text = orch.slack.chat_postMessage.call_args.kwargs["text"]
        assert "already exists" in text

    def test_hire_from_template_creates_audit_entry(self, orch):
        orch.handle_message("C123", "ts-6", "U001", "hire from template support")
        entries = orch.audit.query(action_filter="intern_created")
        assert len(entries) == 1
        assert entries[0]["details"]["name"] == "Support"
        assert entries[0]["details"]["template"] == "support"

    def test_router_recognizes_templates(self):
        from src.router import MessageRouter
        router = MessageRouter([])
        result = router.route("templates")
        assert result.intern_name is None
        assert result.message == "templates"
