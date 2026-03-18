"""Tests for DelegateToInternTool — inter-intern delegation."""
import pytest
from unittest.mock import MagicMock, patch

from src.integrations.intern_store import InternStore
from src.intern_registry import InternRegistry
from src.models.intern import InternJD
from src.tool_registry import ToolRegistry
from src.tools.delegate_tool import DelegateToInternTool


def _make_jd(**overrides) -> InternJD:
    defaults = {
        "name": "Sarah",
        "role": "Research Intern",
        "responsibilities": ["Research topics"],
        "tone": "Professional",
        "tools_allowed": ["web_search"],
        "autonomy_rules": "Always propose",
        "created_by": "U001",
    }
    defaults.update(overrides)
    return InternJD(**defaults)


@pytest.fixture
def intern_store(tmp_path):
    s = InternStore(db_path=str(tmp_path / "test.db"))
    yield s
    s.close()


@pytest.fixture
def intern_registry(intern_store):
    return InternRegistry(intern_store)


@pytest.fixture
def tool_registry():
    return ToolRegistry()


@pytest.fixture
def mock_runner():
    return MagicMock()


@pytest.fixture
def delegate_tool(intern_registry, mock_runner, tool_registry):
    return DelegateToInternTool.create(
        intern_registry=intern_registry,
        crew_runner=mock_runner,
        tool_registry=tool_registry,
        config={},
    )


def _create_intern(registry, **overrides):
    jd = _make_jd(**overrides)
    registry.create_intern(jd)
    return jd


class TestDelegateToValidIntern:
    def test_delegates_and_returns_result(self, delegate_tool, mock_runner, intern_registry):
        _create_intern(intern_registry, name="Sarah", role="Researcher")
        mock_runner.run_for_intern.return_value = "Here are the research findings..."

        result = delegate_tool._run(intern_name="sarah", task="research AI trends")

        assert "Sarah" in result
        assert "research findings" in result
        mock_runner.run_for_intern.assert_called_once()
        call_kwargs = mock_runner.run_for_intern.call_args[1]
        assert call_kwargs["user_message"] == "research AI trends"
        assert call_kwargs["intern_name"] == "Sarah"

    def test_passes_backstory_with_role(self, delegate_tool, mock_runner, intern_registry):
        _create_intern(intern_registry, name="Sarah", role="Research Intern")
        mock_runner.run_for_intern.return_value = "done"

        delegate_tool._run(intern_name="sarah", task="do it")

        call_kwargs = mock_runner.run_for_intern.call_args[1]
        assert "Research Intern" in call_kwargs["intern_backstory"]
        assert "subtask" in call_kwargs["intern_backstory"]


class TestDelegateToUnknownIntern:
    def test_returns_error(self, delegate_tool, intern_registry):
        _create_intern(intern_registry, name="Alex")

        result = delegate_tool._run(intern_name="bob", task="do something")

        assert "No intern named 'bob'" in result
        assert "alex" in result.lower()  # suggests available interns


class TestDelegateActionPlanConversion:
    def test_action_plan_converted_to_text(self, delegate_tool, mock_runner, intern_registry):
        _create_intern(intern_registry, name="Sarah")
        mock_runner.run_for_intern.return_value = {
            "type": "action_plan",
            "summary": "Create a Notion task",
            "steps": [
                {"service": "notion", "action": "create_task", "description": "Create research task"},
            ],
        }

        result = delegate_tool._run(intern_name="sarah", task="create a task")

        assert "proposes" in result
        assert "Create a Notion task" in result
        assert "Create research task" in result
        assert "approval" in result.lower()


class TestDelegateNoRecursion:
    def test_delegatee_does_not_get_delegate_tool(self, delegate_tool, mock_runner, intern_registry, tool_registry):
        _create_intern(intern_registry, name="Sarah", tools_allowed=["web_search", "delegate"])

        # Register a delegation tool and a web search tool
        tool_registry.register_crewai_tool("delegate", delegate_tool)
        mock_search = MagicMock()
        tool_registry.register_crewai_tool("web_search", mock_search)

        mock_runner.run_for_intern.return_value = "done"
        delegate_tool._run(intern_name="sarah", task="search")

        call_kwargs = mock_runner.run_for_intern.call_args[1]
        tools_passed = call_kwargs["tools"]
        # Should have web_search but NOT delegate
        assert mock_search in tools_passed
        assert delegate_tool not in tools_passed


class TestDelegateErrorHandling:
    def test_runner_exception_handled(self, delegate_tool, mock_runner, intern_registry):
        _create_intern(intern_registry, name="Sarah")
        mock_runner.run_for_intern.side_effect = Exception("Crew timeout")

        result = delegate_tool._run(intern_name="sarah", task="something")

        assert "failed" in result.lower()
        assert "Crew timeout" in result

    def test_not_configured(self):
        tool = DelegateToInternTool()
        result = tool._run(intern_name="sarah", task="something")
        assert "not configured" in result.lower()


class TestDelegateWithNotion:
    def test_notion_context_passed(self, mock_runner, intern_registry, tool_registry):
        _create_intern(intern_registry, name="Sarah")
        mock_oauth = MagicMock()
        mock_user_registry = MagicMock()
        mock_brain = MagicMock()
        mock_brain.get_context_for_request.return_value = "Notion: tasks list"

        tool = DelegateToInternTool.create(
            intern_registry=intern_registry,
            crew_runner=mock_runner,
            tool_registry=tool_registry,
            config={},
            notion_oauth=mock_oauth,
            notion_user_registry=mock_user_registry,
        )
        mock_runner.run_for_intern.return_value = "done"

        with patch("src.context.current_user_id") as mock_ctx, \
             patch("src.integrations.notion_second_brain.build_user_second_brain", return_value=mock_brain):
            mock_ctx.get.return_value = "U123"
            tool._run(intern_name="sarah", task="check tasks")

        call_kwargs = mock_runner.run_for_intern.call_args[1]
        assert call_kwargs["notion_context"] == "Notion: tasks list"

    def test_notion_failure_graceful(self, mock_runner, intern_registry, tool_registry):
        _create_intern(intern_registry, name="Sarah")
        mock_oauth = MagicMock()
        mock_user_registry = MagicMock()
        mock_brain = MagicMock()
        mock_brain.get_context_for_request.side_effect = Exception("Notion down")

        tool = DelegateToInternTool.create(
            intern_registry=intern_registry,
            crew_runner=mock_runner,
            tool_registry=tool_registry,
            config={},
            notion_oauth=mock_oauth,
            notion_user_registry=mock_user_registry,
        )
        mock_runner.run_for_intern.return_value = "done"

        with patch("src.context.current_user_id") as mock_ctx, \
             patch("src.integrations.notion_second_brain.build_user_second_brain", return_value=mock_brain):
            mock_ctx.get.return_value = "U123"
            # Should not raise, just skip notion context
            result = tool._run(intern_name="sarah", task="check tasks")
        assert "done" in result
