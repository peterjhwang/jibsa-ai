"""
Approval state machine — tracks pending action plans per Slack thread.

Each thread can be in one of two states:
  IDLE    — normal conversation
  PENDING — waiting for user to approve or reject a proposed plan
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class ApprovalState(Enum):
    IDLE = "idle"
    PENDING = "pending"


@dataclass
class ThreadContext:
    state: ApprovalState = ApprovalState.IDLE
    pending_plan: Optional[dict] = None
    channel: Optional[str] = None
    thread_ts: Optional[str] = None


class ApprovalManager:
    def __init__(self, config: dict):
        approval_cfg = config.get("approval", {})
        self._approve_keywords: list[str] = [
            kw.lower() for kw in approval_cfg.get(
                "approve_keywords", ["✅", "yes", "approved", "go", "go ahead", "do it", "proceed"]
            )
        ]
        self._reject_keywords: list[str] = [
            kw.lower() for kw in approval_cfg.get(
                "reject_keywords", ["❌", "no", "cancel", "stop", "revise", "change"]
            )
        ]
        self._threads: dict[str, ThreadContext] = {}

    def get(self, thread_ts: str) -> ThreadContext:
        if thread_ts not in self._threads:
            self._threads[thread_ts] = ThreadContext()
        return self._threads[thread_ts]

    def set_pending(self, thread_ts: str, plan: dict, channel: str) -> None:
        ctx = self.get(thread_ts)
        ctx.state = ApprovalState.PENDING
        ctx.pending_plan = plan
        ctx.channel = channel
        ctx.thread_ts = thread_ts
        logger.debug("Thread %s — waiting for approval of plan: %s", thread_ts, plan.get("summary"))

    def clear(self, thread_ts: str) -> None:
        self._threads[thread_ts] = ThreadContext()

    def is_approval(self, text: str) -> bool:
        text_lower = text.lower().strip()
        return any(kw in text_lower for kw in self._approve_keywords)

    def is_rejection(self, text: str) -> bool:
        text_lower = text.lower().strip()
        return any(kw in text_lower for kw in self._reject_keywords)
