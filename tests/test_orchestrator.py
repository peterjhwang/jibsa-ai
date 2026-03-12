"""Tests for Orchestrator routing and approval flow."""
from unittest.mock import MagicMock, patch
import pytest

from src.orchestrator import Orchestrator

CONFIG = {
    "jibsa": {"max_history": 20, "claude_timeout": 120, "timezone": "UTC"},
    "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
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
    with patch("src.orchestrator.CrewRunner") as MockRunner, \
         patch("src.orchestrator.build_second_brain", return_value=None):
        mock_runner = MagicMock()
        MockRunner.return_value = mock_runner
        orch = Orchestrator(mock_slack, CONFIG)
        orch.runner = mock_runner
        return orch


def test_conversational_response_posts_text(orchestrator, mock_slack):
    orchestrator.runner.run_for_jibsa.return_value = "Good morning! How can I help?"
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
    orchestrator.runner.run_for_jibsa.return_value = plan
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


# ---------------------------------------------------------------------------
# Routing tests (v0.6)
# ---------------------------------------------------------------------------

def test_hire_message_starts_hire_flow(orchestrator, mock_slack):
    with patch.object(orchestrator.hire_flow, "start_session") as mock_start, \
         patch.object(orchestrator.hire_flow, "handle", return_value="What should this intern do?"):
        orchestrator.handle_message("C123", "ts-5", "U001", "hire a marketing intern")
        mock_start.assert_called_once()


def test_unknown_intern_name_routes_to_jibsa(orchestrator, mock_slack):
    orchestrator.runner.run_for_jibsa.return_value = "I can help with that."
    orchestrator.handle_message("C123", "ts-6", "U001", "bob do something")
    mock_slack.chat_postMessage.assert_called_once()


# ---------------------------------------------------------------------------
# Management commands (v0.6)
# ---------------------------------------------------------------------------

def test_list_interns_command(orchestrator, mock_slack):
    orchestrator.handle_message("C123", "ts-7", "U001", "list interns")
    mock_slack.chat_postMessage.assert_called_once()
    text = mock_slack.chat_postMessage.call_args.kwargs["text"]
    assert "intern" in text.lower()


def test_team_command(orchestrator, mock_slack):
    orchestrator.handle_message("C123", "ts-8", "U001", "team")
    mock_slack.chat_postMessage.assert_called_once()


def test_fire_unknown_intern(orchestrator, mock_slack):
    orchestrator.handle_message("C123", "ts-9", "U001", "fire bob")
    mock_slack.chat_postMessage.assert_called_once()
    text = mock_slack.chat_postMessage.call_args.kwargs["text"]
    assert "⚠️" in text


# ---------------------------------------------------------------------------
# Block Kit button actions
# ---------------------------------------------------------------------------

def test_approve_button_executes_plan(orchestrator, mock_slack):
    plan = {"type": "action_plan", "summary": "Test", "steps": [], "needs_approval": True}
    orchestrator.approval.set_pending("ts-10", plan, "C123")
    respond = MagicMock()
    orchestrator.handle_button_action("approve_plan", "C123", "ts-10", "U001", respond)
    respond.assert_called_once()
    assert "Approved" in respond.call_args[0][0] or "✅" in respond.call_args[0][0]
    from src.approval import ApprovalState
    assert orchestrator.approval.get("ts-10").state == ApprovalState.IDLE


def test_reject_button_clears_plan(orchestrator, mock_slack):
    plan = {"type": "action_plan", "summary": "Test", "steps": [], "needs_approval": True}
    orchestrator.approval.set_pending("ts-11", plan, "C123")
    respond = MagicMock()
    orchestrator.handle_button_action("reject_plan", "C123", "ts-11", "U001", respond)
    respond.assert_called_once()
    assert "Rejected" in respond.call_args[0][0] or "❌" in respond.call_args[0][0]
    from src.approval import ApprovalState
    assert orchestrator.approval.get("ts-11").state == ApprovalState.IDLE


def test_button_action_no_pending_plan(orchestrator, mock_slack):
    respond = MagicMock()
    orchestrator.handle_button_action("approve_plan", "C123", "ts-12", "U001", respond)
    respond.assert_called_once()
    assert "No pending" in respond.call_args[0][0]


# ---------------------------------------------------------------------------
# Block Kit plan formatting
# ---------------------------------------------------------------------------

def test_format_plan_blocks_structure(orchestrator):
    plan = {
        "summary": "Create task",
        "steps": [{"description": "Create a task in Notion", "service": "notion", "action": "create_task"}],
    }
    blocks = orchestrator._format_plan_blocks(plan, "fallback text")
    assert len(blocks) == 3
    # Section with summary
    assert blocks[0]["type"] == "section"
    assert "Create task" in blocks[0]["text"]["text"]
    # Section with steps
    assert blocks[1]["type"] == "section"
    assert "Create a task in Notion" in blocks[1]["text"]["text"]
    # Actions with buttons
    assert blocks[2]["type"] == "actions"
    assert len(blocks[2]["elements"]) == 2
    action_ids = {el["action_id"] for el in blocks[2]["elements"]}
    assert action_ids == {"approve_plan", "reject_plan"}


def test_action_plan_posts_blocks(orchestrator, mock_slack):
    plan = {
        "type": "action_plan",
        "summary": "Create task",
        "steps": [{"service": "notion", "action": "create_task", "params": {}, "description": "Create task"}],
        "needs_approval": True,
    }
    orchestrator.runner.run_for_jibsa.return_value = plan
    orchestrator.handle_message("C123", "ts-13", "U001", "create a task for code review")
    # Should post with blocks
    call_kwargs = mock_slack.chat_postMessage.call_args.kwargs
    assert "blocks" in call_kwargs
    assert any(b["type"] == "actions" for b in call_kwargs["blocks"])


# ---------------------------------------------------------------------------
# Slack execution
# ---------------------------------------------------------------------------

def test_execute_slack_post_message(orchestrator, mock_slack):
    step = {"action": "post_message", "params": {"channel": "#general", "message": "Hello!"}}
    result = orchestrator._execute_slack_step(step)
    assert result["ok"] is True
    mock_slack.chat_postMessage.assert_called_with(channel="#general", text="Hello!")


def test_execute_slack_missing_params(orchestrator, mock_slack):
    step = {"action": "post_message", "params": {"channel": "", "message": ""}}
    result = orchestrator._execute_slack_step(step)
    assert result["ok"] is False
    assert "Missing" in result["error"]


def test_execute_slack_unknown_action(orchestrator, mock_slack):
    step = {"action": "delete_message", "params": {}}
    result = orchestrator._execute_slack_step(step)
    assert result["ok"] is False
    assert "Unknown" in result["error"]
