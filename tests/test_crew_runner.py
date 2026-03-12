"""Tests for CrewRunner — CrewAI-based agent execution."""
from unittest.mock import MagicMock, patch
import pytest

from src.crew_runner import CrewRunner, _extract_json, _build_llm_string


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

def test_extract_json_bare():
    result = _extract_json('{"type": "action_plan", "summary": "test"}')
    assert result["type"] == "action_plan"


def test_extract_json_with_code_fence():
    text = '```json\n{"type": "action_plan", "summary": "test"}\n```'
    result = _extract_json(text)
    assert result["type"] == "action_plan"


def test_extract_json_returns_none_for_plain_text():
    assert _extract_json("Hello, how are you?") is None


def test_extract_json_returns_none_for_invalid_json():
    assert _extract_json("{invalid json}") is None


# ---------------------------------------------------------------------------
# _build_llm_string
# ---------------------------------------------------------------------------

def test_build_llm_string_anthropic():
    config = {"llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"}}
    assert _build_llm_string(config) == "anthropic/claude-sonnet-4-20250514"


def test_build_llm_string_openai():
    config = {"llm": {"provider": "openai", "model": "gpt-4o"}}
    assert _build_llm_string(config) == "openai/gpt-4o"


def test_build_llm_string_already_has_slash():
    config = {"llm": {"provider": "anthropic", "model": "anthropic/claude-sonnet-4-20250514"}}
    assert _build_llm_string(config) == "anthropic/claude-sonnet-4-20250514"


def test_build_llm_string_defaults():
    assert _build_llm_string({}) == "anthropic/claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# CrewRunner init
# ---------------------------------------------------------------------------

def test_crew_runner_init():
    config = {
        "jibsa": {"timezone": "UTC"},
        "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "temperature": 0.5},
    }
    runner = CrewRunner(config)
    assert runner._llm_string == "anthropic/claude-sonnet-4-20250514"
    assert runner._temperature == 0.5


# ---------------------------------------------------------------------------
# CrewRunner.run_for_jibsa (mocked)
# ---------------------------------------------------------------------------

@patch("src.crew_runner.Crew")
@patch("src.crew_runner.Task")
@patch("src.crew_runner.Agent")
def test_run_for_jibsa_returns_string(MockAgent, MockTask, MockCrew):
    mock_result = MagicMock()
    mock_result.raw = "Good morning! How can I help?"
    MockCrew.return_value.kickoff.return_value = mock_result

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = CrewRunner(config)
    result = runner.run_for_jibsa("hello")

    assert isinstance(result, str)
    assert "Good morning" in result


@patch("src.crew_runner.Crew")
@patch("src.crew_runner.Task")
@patch("src.crew_runner.Agent")
def test_run_for_jibsa_returns_dict_for_action_plan(MockAgent, MockTask, MockCrew):
    mock_result = MagicMock()
    mock_result.raw = '{"type": "action_plan", "summary": "Create task", "steps": [], "needs_approval": true}'
    MockCrew.return_value.kickoff.return_value = mock_result

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = CrewRunner(config)
    result = runner.run_for_jibsa("create a task")

    assert isinstance(result, dict)
    assert result["type"] == "action_plan"


@patch("src.crew_runner.Crew")
@patch("src.crew_runner.Task")
@patch("src.crew_runner.Agent")
def test_run_for_jibsa_handles_error(MockAgent, MockTask, MockCrew):
    MockCrew.return_value.kickoff.side_effect = Exception("API timeout")

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = CrewRunner(config)
    result = runner.run_for_jibsa("hello")

    assert isinstance(result, str)
    assert "⚠️" in result


# ---------------------------------------------------------------------------
# CrewRunner.run_for_intern (mocked)
# ---------------------------------------------------------------------------

@patch("src.crew_runner.Crew")
@patch("src.crew_runner.Task")
@patch("src.crew_runner.Agent")
def test_run_for_intern_returns_string(MockAgent, MockTask, MockCrew):
    mock_result = MagicMock()
    mock_result.raw = "Here are 3 LinkedIn post drafts..."
    MockCrew.return_value.kickoff.return_value = mock_result

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = CrewRunner(config)
    result = runner.run_for_intern(
        user_message="write 3 LinkedIn posts",
        intern_name="Alex",
        intern_role="Content Intern",
        intern_backstory="You are Alex, a content marketing intern.",
    )

    assert isinstance(result, str)
    assert "LinkedIn" in result


# ---------------------------------------------------------------------------
# CrewRunner.run_for_team (mocked)
# ---------------------------------------------------------------------------

@patch("src.crew_runner.Crew")
@patch("src.crew_runner.Task")
@patch("src.crew_runner.Agent")
def test_run_for_team_returns_string(MockAgent, MockTask, MockCrew):
    mock_result = MagicMock()
    mock_result.raw = "Here is the combined team analysis..."
    MockCrew.return_value.kickoff.return_value = mock_result

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = CrewRunner(config)
    team = [
        {"name": "Alex", "role": "Dev", "backstory": "You are Alex, a developer.", "tools": []},
        {"name": "Sarah", "role": "QA", "backstory": "You are Sarah, a QA engineer.", "tools": []},
    ]
    result = runner.run_for_team(user_message="review the code", team=team)

    assert isinstance(result, str)
    assert "team analysis" in result


@patch("src.crew_runner.Crew")
@patch("src.crew_runner.Task")
@patch("src.crew_runner.Agent")
def test_run_for_team_returns_action_plan(MockAgent, MockTask, MockCrew):
    mock_result = MagicMock()
    mock_result.raw = '{"type": "action_plan", "summary": "Team plan", "steps": []}'
    MockCrew.return_value.kickoff.return_value = mock_result

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = CrewRunner(config)
    team = [
        {"name": "Alex", "role": "Dev", "backstory": "Dev backstory", "tools": []},
        {"name": "Sarah", "role": "QA", "backstory": "QA backstory", "tools": []},
    ]
    result = runner.run_for_team(user_message="create tasks", team=team)

    assert isinstance(result, dict)
    assert result["type"] == "action_plan"


@patch("src.crew_runner.Crew")
@patch("src.crew_runner.Task")
@patch("src.crew_runner.Agent")
def test_run_for_team_handles_error(MockAgent, MockTask, MockCrew):
    MockCrew.return_value.kickoff.side_effect = Exception("Team API error")

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = CrewRunner(config)
    team = [
        {"name": "Alex", "role": "Dev", "backstory": "Dev backstory", "tools": []},
        {"name": "Sarah", "role": "QA", "backstory": "QA backstory", "tools": []},
    ]
    result = runner.run_for_team(user_message="do something", team=team)

    assert isinstance(result, str)
    assert "⚠️" in result


@patch("src.crew_runner.Crew")
@patch("src.crew_runner.Task")
@patch("src.crew_runner.Agent")
def test_run_for_team_creates_multiple_agents(MockAgent, MockTask, MockCrew):
    mock_result = MagicMock()
    mock_result.raw = "Done"
    MockCrew.return_value.kickoff.return_value = mock_result

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = CrewRunner(config)
    team = [
        {"name": "Alex", "role": "Dev", "backstory": "Dev", "tools": []},
        {"name": "Sarah", "role": "QA", "backstory": "QA", "tools": []},
        {"name": "Bob", "role": "PM", "backstory": "PM", "tools": []},
    ]
    runner.run_for_team(user_message="plan sprint", team=team)

    # Should create 3 agents and 3 tasks
    assert MockAgent.call_count == 3
    assert MockTask.call_count == 3
    # Crew should be initialized with 3 agents and 3 tasks
    crew_call = MockCrew.call_args
    assert len(crew_call.kwargs["agents"]) == 3
    assert len(crew_call.kwargs["tasks"]) == 3


# ---------------------------------------------------------------------------
# CrewRunner.run_for_hire (mocked)
# ---------------------------------------------------------------------------

@patch("src.crew_runner.Crew")
@patch("src.crew_runner.Task")
@patch("src.crew_runner.Agent")
def test_run_for_hire_returns_string(MockAgent, MockTask, MockCrew):
    mock_result = MagicMock()
    mock_result.raw = "What tasks should this intern handle?"
    MockCrew.return_value.kickoff.return_value = mock_result

    config = {"jibsa": {"timezone": "UTC"}, "llm": {"provider": "anthropic"}}
    runner = CrewRunner(config)
    result = runner.run_for_hire("hire a marketing intern", "notion, web_search")

    assert isinstance(result, str)
    assert "tasks" in result.lower() or MockCrew.return_value.kickoff.called
