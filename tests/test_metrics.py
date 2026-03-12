"""Tests for MetricsTracker."""
import time
import pytest
from src.metrics import MetricsTracker, _format_duration


def test_empty_stats():
    tracker = MetricsTracker()
    assert "No requests" in tracker.format_stats()


def test_record_and_format():
    tracker = MetricsTracker()
    tracker.record_request("alex", 2.5)
    tracker.record_request("jibsa", 1.0)
    stats = tracker.format_stats()
    assert "Total requests:" in stats
    assert "2" in stats


def test_per_intern_breakdown():
    tracker = MetricsTracker()
    tracker.record_request("alex", 2.0)
    tracker.record_request("alex", 3.0)
    tracker.record_request("bob", 1.0)
    stats = tracker.format_stats()
    assert "Alex" in stats
    assert "Bob" in stats


def test_action_plan_tracking():
    tracker = MetricsTracker()
    tracker.record_request("alex", 2.0, was_action_plan=True)
    tracker.record_request("alex", 1.0, was_action_plan=False)
    stats = tracker.format_stats()
    assert "Action plans:" in stats
    assert "1" in stats  # 1 action plan


def test_approval_tracking():
    tracker = MetricsTracker()
    tracker.record_request("alex", 2.0, was_action_plan=True)
    tracker.record_approval("alex")
    stats = tracker.format_stats()
    assert "1 approved" in stats


def test_error_tracking():
    tracker = MetricsTracker()
    tracker.record_request("alex", 2.0, error=True)
    stats = tracker.format_stats()
    assert "Errors:" in stats


def test_format_duration():
    assert _format_duration(30) == "30s"
    assert _format_duration(120) == "2m"
    assert _format_duration(7200) == "2.0h"
    assert _format_duration(172800) == "2.0d"
