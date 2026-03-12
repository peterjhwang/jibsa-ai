"""Tests for CircuitBreaker."""
import time
import pytest
from src.circuit_breaker import CircuitBreaker, CircuitState, CircuitOpenError


def test_starts_closed():
    cb = CircuitBreaker("test")
    assert cb.state == CircuitState.CLOSED


def test_stays_closed_on_success():
    cb = CircuitBreaker("test", failure_threshold=3)
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_opens_after_threshold():
    cb = CircuitBreaker("test", failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # not yet
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_check_raises_when_open():
    cb = CircuitBreaker("test", failure_threshold=1)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    with pytest.raises(CircuitOpenError) as exc_info:
        cb.check()
    assert "test" in str(exc_info.value)


def test_check_passes_when_closed():
    cb = CircuitBreaker("test")
    cb.check()  # should not raise


def test_transitions_to_half_open_after_timeout():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN


def test_half_open_closes_on_success():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
    cb.record_failure()
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_half_open_reopens_on_failure():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
    cb.record_failure()
    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_success_resets_failure_count():
    cb = CircuitBreaker("test", failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()  # resets count
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # still below threshold


def test_reset():
    cb = CircuitBreaker("test", failure_threshold=1)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    cb.reset()
    assert cb.state == CircuitState.CLOSED


def test_check_allows_half_open():
    cb = CircuitBreaker("test", failure_threshold=1, recovery_timeout=0.1)
    cb.record_failure()
    time.sleep(0.15)
    cb.check()  # should not raise in half-open
