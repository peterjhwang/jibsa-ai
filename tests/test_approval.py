"""Tests for the ApprovalManager state machine."""
import time

import pytest
from src.approval import ApprovalManager, ApprovalState

CONFIG = {
    "approval": {
        "approve_keywords": ["✅", "yes", "approved", "go", "go ahead", "do it", "proceed"],
        "reject_keywords": ["❌", "no", "cancel", "stop", "revise", "change"],
        "ttl_seconds": 3600,
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


def test_set_pending_records_timestamp(mgr):
    plan = {"type": "action_plan", "summary": "Test", "steps": []}
    before = time.time()
    mgr.set_pending("ts-001", plan, "C123")
    after = time.time()
    ctx = mgr.get("ts-001")
    assert before <= ctx.created_at <= after


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


# ---------------------------------------------------------------------------
# Word-boundary matching — no false positives
# ---------------------------------------------------------------------------

def test_cancel_does_not_match_canary(mgr):
    """'cancel' keyword should not trigger on 'canary'."""
    assert not mgr.is_rejection("canary")


def test_no_does_not_match_notion(mgr):
    """'no' keyword should not trigger on 'notion'."""
    assert not mgr.is_rejection("notion")


def test_go_does_not_match_google(mgr):
    """'go' keyword should not trigger on 'google'."""
    assert not mgr.is_approval("google it")


def test_stop_does_not_match_nonstop(mgr):
    """'stop' keyword should not trigger on 'nonstop'."""
    assert not mgr.is_rejection("nonstop")


def test_yes_in_sentence(mgr):
    """'yes' should match in a sentence."""
    assert mgr.is_approval("yes please")
    assert mgr.is_approval("ok yes")


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------

def test_ttl_expires_pending_plan():
    """Pending plan should expire after TTL."""
    config = {
        "approval": {
            "approve_keywords": ["yes"],
            "reject_keywords": ["no"],
            "ttl_seconds": 1,  # 1 second TTL for testing
        }
    }
    mgr = ApprovalManager(config)
    plan = {"type": "action_plan", "summary": "Test", "steps": []}
    mgr.set_pending("ts-ttl", plan, "C123")

    # Should be pending immediately
    ctx = mgr.get("ts-ttl")
    assert ctx.state == ApprovalState.PENDING

    # Wait for TTL to expire
    time.sleep(1.1)

    # Should be idle after TTL
    ctx = mgr.get("ts-ttl")
    assert ctx.state == ApprovalState.IDLE
    assert ctx.pending_plan is None


def test_cleanup_expired():
    """cleanup_expired() should remove stale entries."""
    config = {
        "approval": {
            "approve_keywords": ["yes"],
            "reject_keywords": ["no"],
            "ttl_seconds": 1,
        }
    }
    mgr = ApprovalManager(config)
    plan = {"type": "action_plan", "summary": "Test", "steps": []}
    mgr.set_pending("ts-a", plan, "C123")
    mgr.set_pending("ts-b", plan, "C123")

    time.sleep(1.1)

    count = mgr.cleanup_expired()
    assert count == 2
    assert mgr.get("ts-a").state == ApprovalState.IDLE
    assert mgr.get("ts-b").state == ApprovalState.IDLE
