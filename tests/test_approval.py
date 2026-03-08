"""Tests for the ApprovalManager state machine."""
import pytest
from src.approval import ApprovalManager, ApprovalState

CONFIG = {
    "approval": {
        "approve_keywords": ["✅", "yes", "approved", "go", "go ahead", "do it", "proceed"],
        "reject_keywords": ["❌", "no", "cancel", "stop", "revise", "change"],
    }
}


@pytest.fixture
def mgr():
    return ApprovalManager(CONFIG)


def test_new_thread_is_idle(mgr):
    ctx = mgr.get("ts-001")
    assert ctx.state == ApprovalState.IDLE


def test_set_pending(mgr):
    plan = {"type": "action_plan", "summary": "Test plan", "steps": []}
    mgr.set_pending("ts-001", plan, "C123")
    ctx = mgr.get("ts-001")
    assert ctx.state == ApprovalState.PENDING
    assert ctx.pending_plan == plan


def test_clear_resets_to_idle(mgr):
    plan = {"type": "action_plan", "summary": "Test", "steps": []}
    mgr.set_pending("ts-001", plan, "C123")
    mgr.clear("ts-001")
    ctx = mgr.get("ts-001")
    assert ctx.state == ApprovalState.IDLE
    assert ctx.pending_plan is None


@pytest.mark.parametrize("text", ["yes", "Yes", "YES", "✅", "go ahead", "do it", "proceed", "approved"])
def test_is_approval(mgr, text):
    assert mgr.is_approval(text)


@pytest.mark.parametrize("text", ["no", "No", "❌", "cancel", "stop", "revise", "change"])
def test_is_rejection(mgr, text):
    assert mgr.is_rejection(text)


def test_ambiguous_is_neither(mgr):
    assert not mgr.is_approval("maybe later")
    assert not mgr.is_rejection("maybe later")
