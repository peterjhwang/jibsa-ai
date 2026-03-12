"""
Metrics — in-memory request tracking for cost visibility.

Tracks per-intern request counts, latency, and estimated token usage.
Provides formatted stats for the `@jibsa stats` command.
"""
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class RequestMetric:
    """A single tracked request."""
    intern_name: str  # "jibsa" for orchestrator requests
    timestamp: float
    latency_s: float
    was_action_plan: bool = False
    was_approved: bool = False
    error: bool = False


class MetricsTracker:
    """In-memory metrics. Resets on restart (intentional — not a billing system)."""

    def __init__(self):
        self._requests: list[RequestMetric] = []
        self._started_at: float = time.time()

    def record_request(
        self,
        intern_name: str,
        latency_s: float,
        was_action_plan: bool = False,
        error: bool = False,
    ) -> None:
        self._requests.append(RequestMetric(
            intern_name=intern_name or "jibsa",
            timestamp=time.time(),
            latency_s=latency_s,
            was_action_plan=was_action_plan,
            error=error,
        ))

    def record_approval(self, intern_name: str) -> None:
        """Mark the last action plan from this intern as approved."""
        for req in reversed(self._requests):
            if req.intern_name == (intern_name or "jibsa") and req.was_action_plan:
                req.was_approved = True
                break

    def format_stats(self) -> str:
        """Format stats for Slack display."""
        if not self._requests:
            return "No requests tracked yet."

        uptime = time.time() - self._started_at
        uptime_str = _format_duration(uptime)

        # Overall stats
        total = len(self._requests)
        errors = sum(1 for r in self._requests if r.error)
        plans = sum(1 for r in self._requests if r.was_action_plan)
        approved = sum(1 for r in self._requests if r.was_approved)
        avg_latency = sum(r.latency_s for r in self._requests) / total

        lines = [
            f"📊 *Jibsa Stats* (uptime: {uptime_str})\n",
            f"*Total requests:* {total}",
            f"*Avg latency:* {avg_latency:.1f}s",
            f"*Action plans:* {plans} ({approved} approved)",
            f"*Errors:* {errors}",
        ]

        # Per-intern breakdown
        by_intern: dict[str, list[RequestMetric]] = defaultdict(list)
        for r in self._requests:
            by_intern[r.intern_name].append(r)

        if len(by_intern) > 1 or (len(by_intern) == 1 and "jibsa" not in by_intern):
            lines.append("\n*Per-intern breakdown:*")
            for name, reqs in sorted(by_intern.items(), key=lambda x: -len(x[1])):
                count = len(reqs)
                avg = sum(r.latency_s for r in reqs) / count
                errs = sum(1 for r in reqs if r.error)
                err_str = f" ({errs} errors)" if errs else ""
                display = name.capitalize() if name != "jibsa" else "Jibsa (orchestrator)"
                lines.append(f"  *{display}:* {count} requests, avg {avg:.1f}s{err_str}")

        return "\n".join(lines)


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    hours = seconds / 3600
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"
