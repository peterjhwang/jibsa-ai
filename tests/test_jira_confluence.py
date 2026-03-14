"""Tests for Jira and Confluence integration — clients, read tools, and orchestrator dispatch."""
import os
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from requests.exceptions import HTTPError

# ---------------------------------------------------------------------------
# JiraClient
# ---------------------------------------------------------------------------

class TestJiraClient:
    @pytest.fixture
    def client(self):
        with patch("src.integrations.jira_client.Jira") as MockJira:
            from src.integrations.jira_client import JiraClient
            mock_jira = MagicMock()
            MockJira.return_value = mock_jira
            client = JiraClient("https://test.atlassian.net", "user@test.com", "token")
            client._mock = mock_jira
            return client

    def test_search_issues(self, client):
        client._mock.jql.return_value = {
            "issues": [{"key": "PROJ-1", "fields": {"summary": "Bug fix"}}]
        }
        results = client.search_issues("project = PROJ")
        assert len(results) == 1
        assert results[0]["key"] == "PROJ-1"

    def test_get_issue(self, client):
        client._mock.issue.return_value = {"key": "PROJ-1", "fields": {"summary": "Test"}}
        issue = client.get_issue("PROJ-1")
        assert issue["key"] == "PROJ-1"

    def test_get_transitions(self, client):
        client._mock.get_issue_transitions.return_value = [
            {"name": "In Progress"}, {"name": "Done"}
        ]
        transitions = client.get_transitions("PROJ-1")
        assert len(transitions) == 2

    def test_create_issue(self, client):
        client._mock.issue_create.return_value = {"key": "PROJ-2"}
        result = client.create_issue("PROJ", "New task", "Task", "Description")
        assert result["key"] == "PROJ-2"
        client._mock.issue_create.assert_called_once()

    def test_update_issue(self, client):
        client._mock.update_issue_field.return_value = {}
        client.update_issue("PROJ-1", {"summary": "Updated"})
        client._mock.update_issue_field.assert_called_once()

    def test_transition_issue(self, client):
        client._mock.get_issue_transitions.return_value = [{"name": "Done"}]
        client._mock.set_issue_status.return_value = {}
        client.transition_issue("PROJ-1", "Done")
        client._mock.set_issue_status.assert_called_once()

    def test_transition_issue_not_found(self, client):
        client._mock.get_issue_transitions.return_value = [{"name": "Done"}]
        with pytest.raises(ValueError, match="not found"):
            client.transition_issue("PROJ-1", "Invalid")

    def test_add_comment(self, client):
        client._mock.issue_add_comment.return_value = {}
        client.add_comment("PROJ-1", "A comment")
        client._mock.issue_add_comment.assert_called_once()

    def test_add_worklog(self, client):
        client._mock.issue_worklog.return_value = {}
        client.add_worklog("PROJ-1", "2h", "Worked on it")
        client._mock.issue_worklog.assert_called_once()

    def test_execute_step_create_issue(self, client):
        client._mock.issue_create.return_value = {"key": "PROJ-3"}
        step = {
            "action": "create_issue",
            "params": {"project_key": "PROJ", "summary": "Test issue"},
            "description": "Create test issue",
        }
        result = client.execute_step(step)
        assert result["ok"] is True
        assert result["key"] == "PROJ-3"
        assert "PROJ-3" in result["url"]

    def test_execute_step_unknown_action(self, client):
        step = {"action": "delete_issue", "params": {}, "description": "Delete"}
        result = client.execute_step(step)
        assert result["ok"] is False
        assert "Unknown" in result["error"]

    def test_execute_step_handles_error(self, client):
        client._mock.issue_create.side_effect = HTTPError("Bad Request")
        step = {
            "action": "create_issue",
            "params": {"project_key": "PROJ", "summary": "Test"},
            "description": "Create",
        }
        result = client.execute_step(step)
        assert result["ok"] is False


class TestJiraRetry:
    def test_retryable_on_429(self):
        from src.integrations.jira_client import _is_retryable
        exc = HTTPError()
        exc.response = MagicMock(status_code=429)
        assert _is_retryable(exc) is True

    def test_retryable_on_500(self):
        from src.integrations.jira_client import _is_retryable
        exc = HTTPError()
        exc.response = MagicMock(status_code=500)
        assert _is_retryable(exc) is True

    def test_not_retryable_on_400(self):
        from src.integrations.jira_client import _is_retryable
        exc = HTTPError()
        exc.response = MagicMock(status_code=400)
        assert _is_retryable(exc) is False

    def test_not_retryable_on_non_http(self):
        from src.integrations.jira_client import _is_retryable
        assert _is_retryable(ValueError("nope")) is False


# ---------------------------------------------------------------------------
# ConfluenceClient
# ---------------------------------------------------------------------------

class TestConfluenceClient:
    @pytest.fixture
    def client(self):
        with patch("src.integrations.confluence_client.Confluence") as MockConf:
            from src.integrations.confluence_client import ConfluenceClient
            mock_conf = MagicMock()
            MockConf.return_value = mock_conf
            client = ConfluenceClient("https://test.atlassian.net", "user@test.com", "token")
            client._mock = mock_conf
            return client

    def test_get_page(self, client):
        client._mock.get_page_by_id.return_value = {"id": "123", "title": "Test Page"}
        page = client.get_page("123")
        assert page["title"] == "Test Page"

    def test_search(self, client):
        client._mock.cql.return_value = {
            "results": [{"title": "Page 1"}, {"title": "Page 2"}]
        }
        results = client.search('text ~ "test"')
        assert len(results) == 2

    def test_get_page_children(self, client):
        client._mock.get_page_child_by_type.return_value = [{"title": "Child"}]
        children = client.get_page_children("123")
        assert len(children) == 1

    def test_create_page(self, client):
        client._mock.create_page.return_value = {
            "id": "456", "title": "New Page",
            "_links": {"base": "https://test.atlassian.net/wiki", "webui": "/spaces/SPACE/pages/456"},
        }
        page = client.create_page("SPACE", "New Page", "<p>Content</p>")
        assert page["id"] == "456"

    def test_update_page(self, client):
        client._mock.update_page.return_value = {"id": "456", "title": "Updated"}
        page = client.update_page("456", "Updated", "<p>New content</p>")
        assert page["title"] == "Updated"

    def test_add_comment(self, client):
        client._mock.add_comment.return_value = {"id": "c1"}
        result = client.add_comment("456", "Nice page!")
        assert result["id"] == "c1"

    def test_execute_step_create_page(self, client):
        client._mock.create_page.return_value = {
            "id": "789", "title": "My Page",
            "_links": {"base": "https://test.atlassian.net/wiki", "webui": "/spaces/SPACE/pages/789"},
        }
        step = {
            "action": "create_page",
            "params": {"space_key": "SPACE", "title": "My Page", "body": "<p>Hi</p>"},
            "description": "Create page",
        }
        result = client.execute_step(step)
        assert result["ok"] is True
        assert "789" in result["url"]

    def test_execute_step_add_comment(self, client):
        client._mock.add_comment.return_value = {"id": "c2"}
        step = {
            "action": "add_comment",
            "params": {"page_id": "456", "body": "Comment"},
            "description": "Add comment",
        }
        result = client.execute_step(step)
        assert result["ok"] is True

    def test_execute_step_unknown_action(self, client):
        step = {"action": "delete_page", "params": {}, "description": "Delete"}
        result = client.execute_step(step)
        assert result["ok"] is False

    def test_execute_step_handles_error(self, client):
        client._mock.create_page.side_effect = HTTPError("Server Error")
        step = {
            "action": "create_page",
            "params": {"space_key": "SP", "title": "T", "body": "B"},
            "description": "Create",
        }
        result = client.execute_step(step)
        assert result["ok"] is False


class TestConfluenceRetry:
    def test_retryable_on_429(self):
        from src.integrations.confluence_client import _is_retryable
        exc = HTTPError()
        exc.response = MagicMock(status_code=429)
        assert _is_retryable(exc) is True

    def test_not_retryable_on_404(self):
        from src.integrations.confluence_client import _is_retryable
        exc = HTTPError()
        exc.response = MagicMock(status_code=404)
        assert _is_retryable(exc) is False


# ---------------------------------------------------------------------------
# JiraReadTool
# ---------------------------------------------------------------------------

class TestJiraReadTool:
    def test_not_connected(self):
        from src.tools.jira_read_tool import JiraReadTool
        tool = JiraReadTool()
        result = tool._run("PROJ-1")
        assert "not connected" in result

    def test_issue_key_lookup(self):
        from src.tools.jira_read_tool import JiraReadTool
        mock_client = MagicMock()
        mock_client.get_issue.return_value = {
            "key": "PROJ-1",
            "fields": {
                "summary": "Fix the bug",
                "status": {"name": "In Progress"},
                "assignee": {"displayName": "Alice"},
                "priority": {"name": "High"},
                "description": "Detailed bug description",
            },
        }
        tool = JiraReadTool.create(mock_client)
        result = tool._run("PROJ-1")
        assert "PROJ-1" in result
        assert "Fix the bug" in result
        assert "In Progress" in result
        assert "Alice" in result

    def test_jql_search(self):
        from src.tools.jira_read_tool import JiraReadTool
        mock_client = MagicMock()
        mock_client.search_issues.return_value = [
            {
                "key": "PROJ-1",
                "fields": {
                    "summary": "Task A",
                    "status": {"name": "Done"},
                    "assignee": {"displayName": "Bob"},
                },
            },
            {
                "key": "PROJ-2",
                "fields": {
                    "summary": "Task B",
                    "status": {"name": "Open"},
                    "assignee": None,
                },
            },
        ]
        tool = JiraReadTool.create(mock_client)
        result = tool._run("project = PROJ ORDER BY created DESC")
        assert "PROJ-1" in result
        assert "PROJ-2" in result
        assert "Task A" in result

    def test_handles_error(self):
        from src.tools.jira_read_tool import JiraReadTool
        mock_client = MagicMock()
        mock_client.search_issues.side_effect = Exception("Connection refused")
        tool = JiraReadTool.create(mock_client)
        result = tool._run("project = PROJ")
        assert "failed" in result


# ---------------------------------------------------------------------------
# ConfluenceReadTool
# ---------------------------------------------------------------------------

class TestConfluenceReadTool:
    def test_not_connected(self):
        from src.tools.confluence_read_tool import ConfluenceReadTool
        tool = ConfluenceReadTool()
        result = tool._run("deployment guide")
        assert "not connected" in result

    def test_search_results(self):
        from src.tools.confluence_read_tool import ConfluenceReadTool
        mock_client = MagicMock()
        mock_client.search.return_value = [
            {
                "title": "Deployment Guide",
                "space": {"name": "Engineering", "key": "ENG"},
                "_links": {"webui": "/wiki/spaces/ENG/pages/123"},
                "excerpt": "<b>How to deploy</b> the application...",
            },
        ]
        tool = ConfluenceReadTool.create(mock_client)
        result = tool._run("deployment guide")
        assert "Deployment Guide" in result
        assert "Engineering" in result

    def test_no_results(self):
        from src.tools.confluence_read_tool import ConfluenceReadTool
        mock_client = MagicMock()
        mock_client.search.return_value = []
        tool = ConfluenceReadTool.create(mock_client)
        result = tool._run("nonexistent page")
        assert "No Confluence pages found" in result

    def test_handles_error(self):
        from src.tools.confluence_read_tool import ConfluenceReadTool
        mock_client = MagicMock()
        mock_client.search.side_effect = Exception("Timeout")
        tool = ConfluenceReadTool.create(mock_client)
        result = tool._run("something")
        assert "failed" in result


# ---------------------------------------------------------------------------
# Orchestrator integration
# ---------------------------------------------------------------------------

_REQUIRED_ENV = {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "ANTHROPIC_API_KEY": "sk-ant-test",
}

_BASE_CONFIG = {
    "jibsa": {"max_history": 20, "claude_timeout": 120, "timezone": "UTC"},
    "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
    "approval": {
        "approve_keywords": ["yes"],
        "reject_keywords": ["no"],
    },
    "integrations": {},
}


class TestOrchestratorJiraDispatch:
    @pytest.fixture
    def orch(self):
        with patch.dict(os.environ, _REQUIRED_ENV), \
             patch("src.orchestrator.CrewRunner") as MockRunner, \
             patch("src.orchestrator.build_second_brain", return_value=None):
            MockRunner.return_value = MagicMock()
            from src.orchestrator import Orchestrator
            o = Orchestrator(MagicMock(), _BASE_CONFIG)
            # Inject mock jira client
            o.jira = MagicMock()
            o.confluence_client = MagicMock()
            return o

    def test_jira_step_dispatches(self, orch):
        orch.jira.execute_step.return_value = {"ok": True, "key": "PROJ-1", "url": "http://jira/PROJ-1"}
        plan = {
            "summary": "Create Jira issue",
            "steps": [{"service": "jira", "action": "create_issue", "params": {"project_key": "PROJ", "summary": "Test"}, "description": "Create issue"}],
        }
        orch._execute_plan(plan, "C123", "ts-1")
        orch.jira.execute_step.assert_called_once()

    def test_confluence_step_dispatches(self, orch):
        orch.confluence_client.execute_step.return_value = {"ok": True, "url": "http://conf/page"}
        plan = {
            "summary": "Create Confluence page",
            "steps": [{"service": "confluence", "action": "create_page", "params": {"space_key": "SP", "title": "T", "body": "B"}, "description": "Create page"}],
        }
        orch._execute_plan(plan, "C123", "ts-2")
        orch.confluence_client.execute_step.assert_called_once()

    def test_jira_circuit_breaker(self, orch):
        from src.circuit_breaker import CircuitState
        orch._jira_circuit._state = CircuitState.OPEN
        orch._jira_circuit._last_failure_time = time.time()

        plan = {
            "summary": "Create issue",
            "steps": [{"service": "jira", "action": "create_issue", "params": {}, "description": "Create"}],
        }
        orch._execute_plan(plan, "C123", "ts-3")
        orch.jira.execute_step.assert_not_called()

    def test_confluence_circuit_breaker(self, orch):
        from src.circuit_breaker import CircuitState
        orch._confluence_circuit._state = CircuitState.OPEN
        orch._confluence_circuit._last_failure_time = time.time()

        plan = {
            "summary": "Create page",
            "steps": [{"service": "confluence", "action": "create_page", "params": {}, "description": "Create"}],
        }
        orch._execute_plan(plan, "C123", "ts-4")
        orch.confluence_client.execute_step.assert_not_called()


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

class TestToolRegistryJiraConf:
    def test_jira_in_catalog(self):
        from src.tool_registry import TOOL_CATALOG
        assert "jira" in TOOL_CATALOG
        assert "create_issue" in TOOL_CATALOG["jira"]["write_actions"]

    def test_confluence_in_catalog(self):
        from src.tool_registry import TOOL_CATALOG
        assert "confluence" in TOOL_CATALOG
        assert "create_page" in TOOL_CATALOG["confluence"]["write_actions"]

    def test_intern_can_use_jira(self):
        from src.tool_registry import ToolRegistry
        from src.models.intern import InternJD
        reg = ToolRegistry()
        intern = InternJD(
            name="Alex", role="Dev", responsibilities=["Code"],
            tone="direct", tools_allowed=["jira"], autonomy_rules="ask first",
        )
        assert reg.can_execute(intern, "jira", "create_issue") is True
        assert reg.can_execute(intern, "jira", "transition_issue") is True
        assert reg.can_execute(intern, "notion", "create_task") is False


# ---------------------------------------------------------------------------
# Valid tool names
# ---------------------------------------------------------------------------

class TestValidToolNames:
    def test_jira_is_valid(self):
        from src.models.intern import VALID_TOOL_NAMES
        assert "jira" in VALID_TOOL_NAMES

    def test_confluence_is_valid(self):
        from src.models.intern import VALID_TOOL_NAMES
        assert "confluence" in VALID_TOOL_NAMES

    def test_intern_validates_with_jira(self):
        from src.models.intern import InternJD
        intern = InternJD(
            name="Alex", role="Dev", responsibilities=["Code"],
            tone="direct", tools_allowed=["jira", "confluence"], autonomy_rules="ask first",
        )
        errors = intern.validate()
        assert not errors
