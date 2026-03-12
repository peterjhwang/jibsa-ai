"""Tests for ToolRegistry — per-intern tool access control."""
from unittest.mock import MagicMock
import pytest

from src.models.intern import InternJD
from src.tool_registry import ToolRegistry, TOOL_CATALOG


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.fixture
def notion_intern():
    return InternJD(
        name="Alex",
        role="Content Intern",
        responsibilities=["Write posts"],
        tone="Professional",
        tools_allowed=["notion"],
        autonomy_rules="Always propose",
    )


@pytest.fixture
def full_intern():
    return InternJD(
        name="Dev",
        role="Dev Helper",
        responsibilities=["Write code"],
        tone="Technical",
        tools_allowed=["notion", "web_search", "code_exec"],
        autonomy_rules="Propose for writes",
    )


@pytest.fixture
def no_tools_intern():
    return InternJD(
        name="Chat",
        role="Chat Buddy",
        responsibilities=["Chat"],
        tone="Friendly",
        tools_allowed=[],
        autonomy_rules="Fully autonomous",
    )


# ---------------------------------------------------------------------------
# Tool filtering
# ---------------------------------------------------------------------------

def test_get_tools_for_intern_filters_correctly(registry, notion_intern):
    tools = registry.get_tools_for_intern(notion_intern)
    assert "notion" in tools
    assert len(tools) == 1


def test_full_intern_gets_all_tools(registry, full_intern):
    tools = registry.get_tools_for_intern(full_intern)
    assert "notion" in tools
    assert "web_search" in tools
    assert "code_exec" in tools


def test_no_tools_intern_gets_empty(registry, no_tools_intern):
    tools = registry.get_tools_for_intern(no_tools_intern)
    assert tools == {}


def test_unknown_tool_name_ignored(registry):
    intern = InternJD(
        name="X", role="X", responsibilities=[], tone="", tools_allowed=["notion", "teleporter"],
        autonomy_rules="",
    )
    tools = registry.get_tools_for_intern(intern)
    assert "notion" in tools
    assert "teleporter" not in tools


# ---------------------------------------------------------------------------
# Permission checking
# ---------------------------------------------------------------------------

def test_can_execute_allowed_write_action(registry, notion_intern):
    assert registry.can_execute(notion_intern, "notion", "create_task") is True


def test_can_execute_denied_service(registry, notion_intern):
    assert registry.can_execute(notion_intern, "jira", "create_issue") is False


def test_can_execute_denied_write_action(registry, notion_intern):
    assert registry.can_execute(notion_intern, "notion", "fly_to_moon") is False


def test_can_execute_no_tools(registry, no_tools_intern):
    assert registry.can_execute(no_tools_intern, "notion", "create_task") is False


def test_can_execute_read_only_tool(registry, full_intern):
    # web_search has no write_actions — always allowed
    assert registry.can_execute(full_intern, "web_search", "search") is True


# ---------------------------------------------------------------------------
# CrewAI tool instances
# ---------------------------------------------------------------------------

def test_register_and_get_crewai_tools(registry, notion_intern):
    mock_tool = MagicMock()
    registry.register_crewai_tool("notion", mock_tool)
    tools = registry.get_crewai_tools_for_intern(notion_intern)
    assert mock_tool in tools


def test_get_crewai_tools_for_jibsa(registry):
    mock_tool1 = MagicMock()
    mock_tool2 = MagicMock()
    registry.register_crewai_tool("notion", mock_tool1)
    registry.register_crewai_tool("web_search", mock_tool2)
    tools = registry.get_crewai_tools_for_jibsa()
    assert len(tools) == 2


def test_get_crewai_tools_filters_by_allowed(registry, notion_intern):
    mock_notion = MagicMock()
    mock_search = MagicMock()
    registry.register_crewai_tool("notion", mock_notion)
    registry.register_crewai_tool("web_search", mock_search)
    tools = registry.get_crewai_tools_for_intern(notion_intern)
    assert mock_notion in tools
    assert mock_search not in tools


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------

def test_tool_descriptions_for_prompt(registry, notion_intern):
    desc = registry.get_tool_descriptions_for_prompt(notion_intern)
    assert "notion" in desc.lower()


def test_no_tools_description(registry, no_tools_intern):
    desc = registry.get_tool_descriptions_for_prompt(no_tools_intern)
    assert "No tools assigned" in desc


# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------

def test_get_all_tool_names(registry):
    names = registry.get_all_tool_names()
    assert "notion" in names
    assert "web_search" in names
    assert "code_exec" in names
