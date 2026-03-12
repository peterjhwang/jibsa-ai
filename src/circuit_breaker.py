"""
CircuitBreaker — fail-fast pattern for external API calls.

When an external service (Notion, Slack) has consecutive failures,
the circuit breaker opens and immediately returns errors instead of
waiting for timeout on every request.

States:
  CLOSED    → normal operation, all calls pass through
  OPEN      → too many failures, calls rejected immediately
  HALF_OPEN → testing if service recovered (allows 1 call through)
"""
import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when circuit is open and call is rejected."""
    def __init__(self, service: str, retry_after: float):
        self.service = service
        self.retry_after = retry_after
        super().__init__(f"{service} circuit is open, retry in {retry_after:.0f}s")


class CircuitBreaker:
    """Per-service circuit breaker.

    Args:
        service: Name of the service (for logging)
        failure_threshold: Number of consecutive failures before opening
        recovery_timeout: Seconds to wait before trying half-open
    """

    def __init__(self, service: str, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.service = service
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._success_count = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("Circuit %s → HALF_OPEN (testing recovery)", self.service)
        return self._state

    def check(self) -> None:
        """Check if a call is allowed. Raises CircuitOpenError if not."""
        current = self.state
        if current == CircuitState.OPEN:
            retry_after = self.recovery_timeout - (time.time() - self._last_failure_time)
            raise CircuitOpenError(self.service, max(retry_after, 0))

    def record_success(self) -> None:
        """Record a successful call."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info("Circuit %s → CLOSED (service recovered)", self.service)
        self._state = CircuitState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "Circuit %s → OPEN (%d consecutive failures, retry in %ds)",
                self.service, self._failure_count, self.recovery_timeout,
            )

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
