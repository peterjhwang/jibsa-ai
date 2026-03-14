"""Tests for SOPFlowManager — conversational SOP creation."""
import json
import pytest
from unittest.mock import MagicMock

from src.integrations.sop_store import SOPStore
from src.sop_registry import SOPRegistry
from src.sop_flow import SOPFlowManager, SOPFlowState, _extract_sop_json, _validate_sop_data
from src.tool_registry import ToolRegistry


@pytest.fixture
def sop_store(tmp_path):
    s = SOPStore(db_path=str(tmp_path / "test.db"))
    yield s
    s.close()


@pytest.fixture
def sop_registry(sop_store):
    return SOPRegistry(sop_store)


@pytest.fixture
def mock_runner():
    return MagicMock()


@pytest.fixture
def tool_registry():
    return ToolRegistry()


@pytest.fixture
def flow(mock_runner, sop_registry, tool_registry):
    return SOPFlowManager(mock_runner, sop_registry, tool_registry)


class TestExtractSopJson:
    def test_bare_json(self):
        text = json.dumps({"type": "sop", "name": "my-sop"})
        result = _extract_sop_json(text)
        assert result["name"] == "my-sop"

    def test_code_block(self):
        text = '```json\n{"type": "sop", "name": "my-sop"}\n```'
        result = _extract_sop_json(text)
        assert result["name"] == "my-sop"

    def test_not_sop_type(self):
        text = json.dumps({"type": "other", "name": "x"})
        assert _extract_sop_json(text) is None

    def test_invalid_json(self):
        assert _extract_sop_json("not json at all") is None


class TestValidateSopData:
    def test_valid(self):
        data = {
            "name": "my-sop",
            "trigger_keywords": ["test"],
            "description": "desc",
            "steps": ["step1"],
            "expected_output": "output",
        }
        assert _validate_sop_data(data) == []

    def test_missing_name(self):
        data = {"trigger_keywords": ["test"], "description": "d", "steps": ["s"], "expected_output": "o"}
        errors = _validate_sop_data(data)
        assert len(errors) > 0


class TestSOPFlowManager:
    def test_start_session(self, flow):
        flow.start_session("ts1", "U001", "add sop", intern_name="alex")
        assert flow.has_session("ts1")
        session = flow.get_session("ts1")
        assert session.intern_name == "alex"
        assert session.state == SOPFlowState.GATHERING

    def test_no_session(self, flow):
        assert not flow.has_session("ts1")
        result = flow.handle("ts1", "U001", "hello")
        assert "No active" in result

    def test_gathering_returns_runner_response(self, flow, mock_runner):
        mock_runner.run_for_sop_creation.return_value = "What should the SOP be called?"
        flow.start_session("ts1", "U001", "add sop")
        response = flow.handle("ts1", "U001", "I want a weekly report SOP")
        assert response == "What should the SOP be called?"

    def test_complete_sop_transitions_to_confirming(self, flow, mock_runner):
        sop_json = json.dumps({
            "type": "sop",
            "name": "weekly-report",
            "trigger_keywords": ["weekly", "report"],
            "description": "A weekly report.",
            "steps": ["Query tasks", "Summarize"],
            "expected_output": "A formatted report.",
            "tools_required": [],
            "approval_required": False,
            "priority": 10,
        })
        mock_runner.run_for_sop_creation.return_value = sop_json
        flow.start_session("ts1", "U001", "add sop")
        response = flow.handle("ts1", "U001", "create a weekly report SOP")
        session = flow.get_session("ts1")
        assert session.state == SOPFlowState.CONFIRMING
        assert "weekly-report" in response
        assert "confirm" in response.lower()

    def test_confirmation_approve_creates_sop(self, flow, mock_runner, sop_registry):
        sop_json = json.dumps({
            "type": "sop",
            "name": "test-sop",
            "trigger_keywords": ["test"],
            "description": "A test SOP.",
            "steps": ["Do the thing"],
            "expected_output": "Done.",
            "tools_required": [],
            "approval_required": False,
            "priority": 0,
        })
        mock_runner.run_for_sop_creation.return_value = sop_json
        flow.start_session("ts1", "U001", "add sop")
        flow.handle("ts1", "U001", "create it")
        response = flow.handle("ts1", "U001", "yes")
        assert "created" in response.lower() or "✅" in response
        assert not flow.has_session("ts1")
        # Verify SOP was actually created
        assert sop_registry.get_sop_by_name("test-sop") is not None

    def test_confirmation_reject_cancels(self, flow, mock_runner):
        sop_json = json.dumps({
            "type": "sop",
            "name": "test-sop",
            "trigger_keywords": ["test"],
            "description": "desc",
            "steps": ["s"],
            "expected_output": "o",
        })
        mock_runner.run_for_sop_creation.return_value = sop_json
        flow.start_session("ts1", "U001", "add sop")
        flow.handle("ts1", "U001", "msg")
        response = flow.handle("ts1", "U001", "cancel")
        assert "cancelled" in response.lower()
        assert not flow.has_session("ts1")

    def test_validation_errors_loop_back(self, flow, mock_runner):
        bad_json = json.dumps({
            "type": "sop",
            "name": "",  # invalid
            "trigger_keywords": [],
            "description": "",
            "steps": [],
            "expected_output": "",
        })
        mock_runner.run_for_sop_creation.return_value = bad_json
        flow.start_session("ts1", "U001", "add sop")
        response = flow.handle("ts1", "U001", "msg")
        assert "fixes" in response.lower()
        session = flow.get_session("ts1")
        assert session.state == SOPFlowState.GATHERING

    def test_intern_scope_set(self, flow, mock_runner, sop_registry):
        sop_json = json.dumps({
            "type": "sop",
            "name": "alex-sop",
            "trigger_keywords": ["test"],
            "description": "For alex.",
            "steps": ["Do it"],
            "expected_output": "Done.",
        })
        mock_runner.run_for_sop_creation.return_value = sop_json
        flow.start_session("ts1", "U001", "add sop for alex", intern_name="alex")
        flow.handle("ts1", "U001", "msg")
        flow.handle("ts1", "U001", "yes")
        sop = sop_registry.get_sop_by_name("alex-sop", intern_id="alex")
        assert sop is not None
        assert sop.intern_id == "alex"

    def test_cancel_session(self, flow):
        flow.start_session("ts1", "U001", "add sop")
        flow.cancel_session("ts1")
        assert not flow.has_session("ts1")
