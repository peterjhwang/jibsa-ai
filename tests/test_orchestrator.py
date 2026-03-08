"""Tests for Orchestrator routing and approval flow."""
from unittest.mock import MagicMock, patch
import pytest

from src.orchestrator import Orchestrator

CONFIG = {
    "jibsa": {"max_history": 20, "claude_timeout": 120, "timezone": "UTC"},
    "approval": {
        "approve_keywords": ["✅", "yes", "approved", "go", "go ahead", "do it", "proceed"],
        "reject_keywords": ["❌", "no", "cancel", "stop", "revise", "change"],
    },
    "integrations": {},
}


@pytest.fixture
def mock_slack():
    return MagicMock()


@pytest.fixture
def orchestrator(mock_slack):
    with patch("src.orchestrator.ClaudeRunner") as MockRunner:
        mock_runner = MagicMock()
        MockRunner.return_value = mock_runner
        orch = Orchestrator(mock_slack, CONFIG)
        orch.runner = mock_runner
        return orch


def test_conversational_response_posts_text(orchestrator, mock_slack):
    orchestrator.runner.run.return_value = "Good morning! How can I help?"
    orchestrator.handle_message("C123", "ts-1", "U001", "hello")
    mock_slack.chat_postMessage.assert_called_once()
    args = mock_slack.chat_postMessage.call_args
    assert "Good morning" in args.kwargs["text"]


def test_action_plan_response_posts_plan_and_sets_pending(orchestrator, mock_slack):
    plan = {
        "type": "action_plan",
        "summary": "Create Jira ticket",
        "steps": [{"service": "jira", "action": "create_issue", "params": {}, "description": "Create ticket"}],
        "needs_approval": True,
    }
    orchestrator.runner.run.return_value = plan
    orchestrator.handle_message("C123", "ts-2", "U001", "create a jira ticket for the bug")

    mock_slack.chat_postMessage.assert_called_once()
    text = mock_slack.chat_postMessage.call_args.kwargs["text"]
    assert "📋" in text
    assert "Create Jira ticket" in text
    assert "✅" in text

    from src.approval import ApprovalState
    ctx = orchestrator.approval.get("ts-2")
    assert ctx.state == ApprovalState.PENDING


def test_approval_executes_plan(orchestrator, mock_slack):
    plan = {
        "type": "action_plan",
        "summary": "Test plan",
        "steps": [],
        "needs_approval": True,
    }
    orchestrator.approval.set_pending("ts-3", plan, "C123")
    orchestrator.handle_message("C123", "ts-3", "U001", "yes")

    # Should have posted execution confirmation
    mock_slack.chat_postMessage.assert_called_once()
    from src.approval import ApprovalState
    assert orchestrator.approval.get("ts-3").state == ApprovalState.IDLE


def test_rejection_clears_pending(orchestrator, mock_slack):
    plan = {"type": "action_plan", "summary": "Test", "steps": [], "needs_approval": True}
    orchestrator.approval.set_pending("ts-4", plan, "C123")
    orchestrator.handle_message("C123", "ts-4", "U001", "no")

    from src.approval import ApprovalState
    assert orchestrator.approval.get("ts-4").state == ApprovalState.IDLE
    text = mock_slack.chat_postMessage.call_args.kwargs["text"]
    assert "change" in text.lower() or "revise" in text.lower() or "what" in text.lower()
