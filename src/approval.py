"""
Approval state machine — tracks pending action plans per Slack thread.

Each thread can be in one of two states:
  IDLE    — normal conversation
  PENDING — waiting for user to approve or reject a proposed plan
"""
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 3600  # 1 hour


class ApprovalState(Enum):
    IDLE = "idle"
    PENDING = "pending"


@dataclass
class ThreadContext:
    state: ApprovalState = ApprovalState.IDLE
    pending_plan: Optional[dict] = None
    channel: Optional[str] = None
    thread_ts: Optional[str] = None
    created_at: float = 0.0


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
        self._ttl_seconds: int = approval_cfg.get("ttl_seconds", DEFAULT_TTL_SECONDS)
        self._threads: dict[str, ThreadContext] = {}

    def get(self, thread_ts: str) -> ThreadContext:
        if thread_ts not in self._threads:
            self._threads[thread_ts] = ThreadContext()
            return self._threads[thread_ts]

        ctx = self._threads[thread_ts]
        # Check TTL — expire stale pending plans
        if ctx.state == ApprovalState.PENDING and ctx.created_at > 0:
            if time.time() - ctx.created_at > self._ttl_seconds:
                logger.info("Thread %s — pending plan expired (TTL %ds)", thread_ts, self._ttl_seconds)
                self._threads[thread_ts] = ThreadContext()
        return self._threads[thread_ts]

    def set_pending(self, thread_ts: str, plan: dict, channel: str) -> None:
        ctx = self.get(thread_ts)
        ctx.state = ApprovalState.PENDING
        ctx.pending_plan = plan
        ctx.channel = channel
        ctx.thread_ts = thread_ts
        ctx.created_at = time.time()
        logger.debug("Thread %s — waiting for approval of plan: %s", thread_ts, plan.get("summary"))

    def clear(self, thread_ts: str) -> None:
        self._threads[thread_ts] = ThreadContext()

    def cleanup_expired(self) -> int:
        """Remove all expired pending plans. Returns count of cleaned entries."""
        now = time.time()
        expired = [
            ts for ts, ctx in self._threads.items()
            if ctx.state == ApprovalState.PENDING
            and ctx.created_at > 0
            and now - ctx.created_at > self._ttl_seconds
        ]
        for ts in expired:
            self._threads[ts] = ThreadContext()
        return len(expired)

    def is_approval(self, text: str) -> bool:
        return self._match_keywords(text, self._approve_keywords)

    def is_rejection(self, text: str) -> bool:
        return self._match_keywords(text, self._reject_keywords)

    @staticmethod
    def _match_keywords(text: str, keywords: list[str]) -> bool:
        """Match keywords using word boundaries to avoid false positives.

        Emoji keywords (non-alphanumeric) use simple 'in' matching.
        Word keywords use regex word-boundary matching so that e.g.
        "canary" does not match "cancel".
        """
        text_lower = text.lower().strip()
        for kw in keywords:
            # Emoji or special-char keywords: simple substring match
            if not kw.isascii() or not any(c.isalnum() for c in kw):
                if kw in text_lower:
                    return True
            else:
                # Word-boundary match for alphanumeric keywords
                if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                    return True
        return False
