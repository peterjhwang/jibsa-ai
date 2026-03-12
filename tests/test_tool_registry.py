"""Tests for ToolRegistry — per-intern tool access control."""
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

def test_can_execute_allowed_action(registry, notion_intern):
    assert registry.can_execute(notion_intern, "notion", "create_task") is True


def test_can_execute_denied_service(registry, notion_intern):
    assert registry.can_execute(notion_intern, "jira", "create_issue") is False


def test_can_execute_denied_action(registry, notion_intern):
    # notion is allowed but "fly_to_moon" is not a valid action
    assert registry.can_execute(notion_intern, "notion", "fly_to_moon") is False


def test_can_execute_no_tools(registry, no_tools_intern):
    assert registry.can_execute(no_tools_intern, "notion", "create_task") is False


# ---------------------------------------------------------------------------
# Prompt generation
# ---------------------------------------------------------------------------

def test_tool_descriptions_for_prompt(registry, notion_intern):
    desc = registry.get_tool_descriptions_for_prompt(notion_intern)
    assert "notion" in desc.lower()
    assert "create_task" in desc


def test_no_tools_description(registry, no_tools_intern):
    desc = registry.get_tool_descriptions_for_prompt(no_tools_intern)
    assert "No tools assigned" in desc


# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------

def test_get_all_tool_names(registry):
    names = registry.get_all_tool_names()
    assert "notion" in names
