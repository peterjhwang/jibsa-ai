"""Tests for HireFlowManager — conversational intern creation."""
import json
from unittest.mock import MagicMock
import pytest

from src.hire_flow import HireFlowManager, HireFlowState, _extract_intern_jd, _validate_jd_data
from src.tool_registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_runner():
    return MagicMock()


@pytest.fixture
def mock_registry():
    reg = MagicMock()
    reg.create_intern.return_value = {"ok": True, "page_id": "p1", "url": "https://notion.so/p1"}
    return reg


@pytest.fixture
def tool_registry():
    return ToolRegistry()


@pytest.fixture
def hire_manager(mock_runner, mock_registry, tool_registry):
    return HireFlowManager(mock_runner, mock_registry, tool_registry)


# ---------------------------------------------------------------------------
# _extract_intern_jd
# ---------------------------------------------------------------------------

def test_extract_jd_from_bare_json():
    jd = _extract_intern_jd('{"type": "intern_jd", "name": "Alex", "role": "Content"}')
    assert jd is not None
    assert jd["name"] == "Alex"


def test_extract_jd_from_code_fence():
    text = '```json\n{"type": "intern_jd", "name": "Alex", "role": "Content"}\n```'
    jd = _extract_intern_jd(text)
    assert jd is not None


def test_extract_jd_returns_none_for_conversation():
    assert _extract_intern_jd("What tasks should this intern handle?") is None


def test_extract_jd_returns_none_for_wrong_type():
    assert _extract_intern_jd('{"type": "action_plan", "summary": "test"}') is None


# ---------------------------------------------------------------------------
# _validate_jd_data
# ---------------------------------------------------------------------------

def test_validate_valid_jd():
    jd = {
        "name": "Alex", "role": "Content Intern",
        "responsibilities": ["Write posts"],
        "tools_allowed": ["notion"],
    }
    assert _validate_jd_data(jd) == []


def test_validate_missing_name():
    jd = {"name": "", "role": "Role", "responsibilities": ["X"]}
    errors = _validate_jd_data(jd)
    assert len(errors) > 0


def test_validate_missing_responsibilities():
    jd = {"name": "Alex", "role": "Role", "responsibilities": []}
    errors = _validate_jd_data(jd)
    assert len(errors) > 0


def test_validate_invalid_tool():
    jd = {
        "name": "Alex", "role": "Role",
        "responsibilities": ["X"],
        "tools_allowed": ["teleporter"],
    }
    errors = _validate_jd_data(jd)
    assert any("teleporter" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def test_start_session(hire_manager):
    hire_manager.start_session("ts-1", "U001", "hire a marketing intern")
    assert hire_manager.has_session("ts-1")
    session = hire_manager.get_session("ts-1")
    assert session.state == HireFlowState.GATHERING
    assert len(session.conversation) == 1


def test_has_session_false(hire_manager):
    assert hire_manager.has_session("nonexistent") is False


def test_cancel_session(hire_manager):
    hire_manager.start_session("ts-1", "U001", "hire")
    hire_manager.cancel_session("ts-1")
    assert hire_manager.has_session("ts-1") is False


# ---------------------------------------------------------------------------
# Gathering phase
# ---------------------------------------------------------------------------

def test_gathering_forwards_to_crew(hire_manager, mock_runner):
    mock_runner.run_for_hire.return_value = "What tasks should this intern handle?"
    hire_manager.start_session("ts-1", "U001", "hire a marketing intern")
    response = hire_manager.handle("ts-1", "U001", "hire a marketing intern")
    assert mock_runner.run_for_hire.called


def test_gathering_adds_to_conversation(hire_manager, mock_runner):
    mock_runner.run_for_hire.return_value = "What tasks?"
    hire_manager.start_session("ts-1", "U001", "hire")
    hire_manager.handle("ts-1", "U001", "hire")
    session = hire_manager.get_session("ts-1")
    assert len(session.conversation) >= 2


# ---------------------------------------------------------------------------
# JD completion → CONFIRMING state
# ---------------------------------------------------------------------------

def test_complete_jd_transitions_to_confirming(hire_manager, mock_runner):
    jd_json = json.dumps({
        "type": "intern_jd",
        "name": "Alex",
        "role": "Content Marketing Intern",
        "responsibilities": ["Write LinkedIn posts"],
        "tone": "Professional",
        "tools_allowed": ["notion"],
        "autonomy_rules": "Always propose",
    })
    mock_runner.run_for_hire.return_value = jd_json

    hire_manager.start_session("ts-1", "U001", "hire a content intern")
    response = hire_manager.handle("ts-1", "U001", "hire a content intern")

    assert "Alex" in response
    assert "Content Marketing Intern" in response
    assert "✅" in response

    session = hire_manager.get_session("ts-1")
    assert session.state == HireFlowState.CONFIRMING


def test_invalid_jd_asks_for_fixes(hire_manager, mock_runner):
    """JD with missing role should not confirm, should ask for fixes."""
    jd_json = json.dumps({
        "type": "intern_jd",
        "name": "Alex",
        "role": "",  # Invalid: missing role
        "responsibilities": ["Write posts"],
    })
    mock_runner.run_for_hire.return_value = jd_json

    hire_manager.start_session("ts-1", "U001", "hire")
    response = hire_manager.handle("ts-1", "U001", "hire")

    assert "fixes" in response.lower() or "missing" in response.lower()
    session = hire_manager.get_session("ts-1")
    assert session.state == HireFlowState.GATHERING  # still gathering, not confirming


# ---------------------------------------------------------------------------
# Confirmation phase
# ---------------------------------------------------------------------------

def test_approval_creates_intern(hire_manager, mock_runner, mock_registry):
    jd_json = json.dumps({
        "type": "intern_jd", "name": "Alex", "role": "Content Intern",
        "responsibilities": ["Write"], "tone": "Pro", "tools_allowed": ["notion"],
        "autonomy_rules": "Propose",
    })
    mock_runner.run_for_hire.return_value = jd_json
    hire_manager.start_session("ts-1", "U001", "hire")
    hire_manager.handle("ts-1", "U001", "hire")

    response = hire_manager.handle("ts-1", "U001", "yes")
    assert "ready" in response.lower() or "✅" in response
    mock_registry.create_intern.assert_called_once()
    assert not hire_manager.has_session("ts-1")


def test_rejection_cancels_session(hire_manager, mock_runner):
    jd_json = json.dumps({
        "type": "intern_jd", "name": "Alex", "role": "Content Intern",
        "responsibilities": ["Write"], "tools_allowed": ["notion"],
    })
    mock_runner.run_for_hire.return_value = jd_json
    hire_manager.start_session("ts-1", "U001", "hire")
    hire_manager.handle("ts-1", "U001", "hire")

    response = hire_manager.handle("ts-1", "U001", "cancel")
    assert "cancel" in response.lower()
    assert not hire_manager.has_session("ts-1")


def test_revision_goes_back_to_gathering(hire_manager, mock_runner):
    jd_json = json.dumps({
        "type": "intern_jd", "name": "Alex", "role": "Content Intern",
        "responsibilities": ["Write"], "tools_allowed": ["notion"],
    })
    mock_runner.run_for_hire.return_value = jd_json
    hire_manager.start_session("ts-1", "U001", "hire")
    hire_manager.handle("ts-1", "U001", "hire")

    mock_runner.run_for_hire.return_value = "Sure, what would you like to change?"
    response = hire_manager.handle("ts-1", "U001", "change the role to Dev Helper")

    session = hire_manager.get_session("ts-1")
    assert session.state == HireFlowState.GATHERING


# ---------------------------------------------------------------------------
# Create failure
# ---------------------------------------------------------------------------

def test_create_failure_returns_error(hire_manager, mock_runner, mock_registry):
    jd_json = json.dumps({
        "type": "intern_jd", "name": "Alex", "role": "Content Intern",
        "responsibilities": ["Write"], "tools_allowed": ["notion"],
    })
    mock_runner.run_for_hire.return_value = jd_json
    hire_manager.start_session("ts-1", "U001", "hire")
    hire_manager.handle("ts-1", "U001", "hire")

    mock_registry.create_intern.return_value = {"ok": False, "error": "DB not configured"}
    response = hire_manager.handle("ts-1", "U001", "yes")
    assert "⚠️" in response
    assert "DB not configured" in response
