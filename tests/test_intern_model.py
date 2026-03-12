"""Tests for InternJD data model — validation, memory, formatting."""
import pytest

from src.models.intern import InternJD, VALID_TOOL_NAMES


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_valid_jd_passes():
    intern = InternJD(
        name="Alex", role="Content Intern",
        responsibilities=["Write posts"], tone="Professional",
        tools_allowed=["notion"], autonomy_rules="Always propose",
    )
    assert intern.validate() == []


def test_missing_name_fails():
    intern = InternJD(
        name="", role="Content Intern",
        responsibilities=["Write posts"], tone="",
        tools_allowed=[], autonomy_rules="",
    )
    errors = intern.validate()
    assert any("Name" in e for e in errors)


def test_missing_role_fails():
    intern = InternJD(
        name="Alex", role="",
        responsibilities=["Write posts"], tone="",
        tools_allowed=[], autonomy_rules="",
    )
    errors = intern.validate()
    assert any("Role" in e for e in errors)


def test_empty_responsibilities_fails():
    intern = InternJD(
        name="Alex", role="Content Intern",
        responsibilities=[], tone="",
        tools_allowed=[], autonomy_rules="",
    )
    errors = intern.validate()
    assert any("responsibility" in e.lower() for e in errors)


def test_name_too_long_fails():
    intern = InternJD(
        name="A" * 31, role="Role",
        responsibilities=["Do stuff"], tone="",
        tools_allowed=[], autonomy_rules="",
    )
    errors = intern.validate()
    assert any("30" in e for e in errors)


def test_invalid_tool_name_caught():
    intern = InternJD(
        name="Alex", role="Role",
        responsibilities=["Do stuff"], tone="",
        tools_allowed=["notion", "teleporter"], autonomy_rules="",
    )
    errors = intern.validate()
    assert any("teleporter" in e.lower() for e in errors)


def test_valid_tool_names_pass():
    intern = InternJD(
        name="Alex", role="Role",
        responsibilities=["Do stuff"], tone="",
        tools_allowed=["notion", "web_search", "code_exec"],
        autonomy_rules="",
    )
    assert intern.validate() == []


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

def test_add_memory():
    intern = InternJD(
        name="Alex", role="Role", responsibilities=["X"],
        tone="", tools_allowed=[], autonomy_rules="",
    )
    intern.add_memory("User prefers bullet points")
    assert len(intern.memory) == 1
    assert "bullet points" in intern.memory[0]


def test_memory_caps_at_20():
    intern = InternJD(
        name="Alex", role="Role", responsibilities=["X"],
        tone="", tools_allowed=[], autonomy_rules="",
    )
    for i in range(25):
        intern.add_memory(f"Memory {i}")
    assert len(intern.memory) == 20
    assert "Memory 24" in intern.memory[-1]


def test_get_memory_context_empty():
    intern = InternJD(
        name="Alex", role="Role", responsibilities=["X"],
        tone="", tools_allowed=[], autonomy_rules="",
    )
    assert intern.get_memory_context() == ""


def test_get_memory_context_with_entries():
    intern = InternJD(
        name="Alex", role="Role", responsibilities=["X"],
        tone="", tools_allowed=[], autonomy_rules="",
    )
    intern.add_memory("User likes concise answers")
    context = intern.get_memory_context()
    assert "Memory" in context
    assert "concise" in context


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def test_format_jd():
    intern = InternJD(
        name="Alex", role="Content Intern",
        responsibilities=["Write posts", "Track metrics"],
        tone="Professional", tools_allowed=["notion", "web_search"],
        autonomy_rules="Always propose",
    )
    formatted = intern.format_jd()
    assert "Alex" in formatted
    assert "Content Intern" in formatted
    assert "Write posts" in formatted
    assert "notion, web_search" in formatted


# ---------------------------------------------------------------------------
# matches_name
# ---------------------------------------------------------------------------

def test_matches_name_case_insensitive():
    intern = InternJD(
        name="Alex", role="R", responsibilities=["X"],
        tone="", tools_allowed=[], autonomy_rules="",
    )
    assert intern.matches_name("alex") is True
    assert intern.matches_name("ALEX") is True
    assert intern.matches_name("Bob") is False
