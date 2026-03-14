"""Tests for SOP data model."""
import pytest

from src.models.sop import SOP, VALID_SOP_NAME_RE


def _make_sop(**overrides) -> SOP:
    defaults = {
        "name": "weekly-report",
        "trigger_keywords": ["weekly", "report"],
        "description": "Generate a weekly report.",
        "steps": ["Query tasks", "Summarize"],
        "expected_output": "A formatted report.",
        "tools_required": ["notion"],
        "approval_required": True,
        "priority": 10,
    }
    defaults.update(overrides)
    return SOP(**defaults)


class TestValidate:
    def test_valid_sop(self):
        sop = _make_sop()
        assert sop.validate() == []

    def test_missing_name(self):
        errors = _make_sop(name="").validate()
        assert any("name" in e.lower() for e in errors)

    def test_bad_name_format(self):
        errors = _make_sop(name="Not Kebab Case").validate()
        assert any("kebab" in e.lower() for e in errors)

    def test_name_with_uppercase(self):
        errors = _make_sop(name="Weekly-Report").validate()
        assert any("kebab" in e.lower() for e in errors)

    def test_name_too_long(self):
        errors = _make_sop(name="a" * 51).validate()
        assert any("50" in e for e in errors)

    def test_missing_description(self):
        errors = _make_sop(description="").validate()
        assert any("description" in e.lower() for e in errors)

    def test_missing_steps(self):
        errors = _make_sop(steps=[]).validate()
        assert any("step" in e.lower() for e in errors)

    def test_missing_trigger_keywords(self):
        errors = _make_sop(trigger_keywords=[]).validate()
        assert any("keyword" in e.lower() for e in errors)

    def test_missing_expected_output(self):
        errors = _make_sop(expected_output="").validate()
        assert any("expected" in e.lower() for e in errors)

    def test_invalid_tools(self):
        errors = _make_sop(tools_required=["notion", "invalid_tool"]).validate()
        assert any("unknown" in e.lower() for e in errors)

    def test_valid_empty_tools(self):
        """SOPs with no tools required are valid."""
        assert _make_sop(tools_required=[]).validate() == []

    def test_priority_negative(self):
        errors = _make_sop(priority=-1).validate()
        assert any("priority" in e.lower() for e in errors)

    def test_priority_over_100(self):
        errors = _make_sop(priority=101).validate()
        assert any("priority" in e.lower() for e in errors)

    def test_priority_boundaries(self):
        assert _make_sop(priority=0).validate() == []
        assert _make_sop(priority=100).validate() == []


class TestNameRegex:
    @pytest.mark.parametrize("name", [
        "weekly-report", "a", "abc", "my-sop-123", "x-y-z",
    ])
    def test_valid_names(self, name):
        assert VALID_SOP_NAME_RE.match(name)

    @pytest.mark.parametrize("name", [
        "Weekly-Report", "UPPER", "has space", "trailing-", "-leading",
        "double--dash", "special!char",
    ])
    def test_invalid_names(self, name):
        assert not VALID_SOP_NAME_RE.match(name)


class TestFormatSop:
    def test_format_includes_fields(self):
        sop = _make_sop(intern_id="alex")
        text = sop.format_sop()
        assert "weekly-report" in text
        assert "alex" in text
        assert "Query tasks" in text
        assert "notion" in text

    def test_format_shared_scope(self):
        sop = _make_sop(intern_id=None)
        text = sop.format_sop()
        assert "Shared" in text


class TestBuildTaskDescription:
    def test_contains_sop_name(self):
        desc = _make_sop().build_task_description("show me the report")
        assert "weekly-report" in desc

    def test_contains_steps(self):
        desc = _make_sop().build_task_description("do it")
        assert "1. Query tasks" in desc
        assert "2. Summarize" in desc

    def test_contains_user_message(self):
        desc = _make_sop().build_task_description("give me the weekly summary")
        assert "give me the weekly summary" in desc

    def test_approval_required_includes_action_plan(self):
        desc = _make_sop(approval_required=True).build_task_description("x")
        assert "action_plan" in desc

    def test_no_approval_responds_directly(self):
        desc = _make_sop(approval_required=False).build_task_description("x")
        assert "Respond directly" in desc


class TestBuildExpectedOutput:
    def test_returns_expected_output(self):
        sop = _make_sop(expected_output="A nice report.")
        assert sop.build_expected_output() == "A nice report."

    def test_fallback_when_empty(self):
        sop = _make_sop(expected_output="")
        assert "SOP" in sop.build_expected_output()
