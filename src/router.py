"""
MessageRouter — parses incoming Slack messages to determine the target intern.

Patterns:
  "@jibsa alex do X"       → ("alex", "do X")
  "@jibsa ask alex to do X" → ("alex", "do X")
  "@jibsa hire ..."        → (None, "hire ...", True)
  "@jibsa do X"            → (None, "do X", False)
"""
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

HIRE_KEYWORDS = {"hire", "create intern", "new intern", "add intern"}


@dataclass
class RouteResult:
    intern_name: Optional[str]  # None = Jibsa orchestrator
    message: str                # cleaned message for the target
    is_hire: bool = False       # hire flow trigger


class MessageRouter:
    def __init__(self, known_names: list[str]):
        """known_names: list of active intern names (case-insensitive lookup)."""
        self._names: set[str] = {n.lower() for n in known_names}

    def update_names(self, names: list[str]) -> None:
        """Update known intern names (e.g. after a hire)."""
        self._names = {n.lower() for n in names}

    def route(self, text: str) -> RouteResult:
        """
        Parse a message and return routing info.

        The Slack mention prefix (<@BOT_ID>) is already stripped by Slack Bolt
        for app_mention events, but may remain in channel messages. We strip
        any leading <@...> or @jibsa prefix.
        """
        cleaned = re.sub(r"^<@\w+>\s*", "", text).strip()

        # Check for hire intent
        cleaned_lower = cleaned.lower()
        for kw in HIRE_KEYWORDS:
            if cleaned_lower.startswith(kw):
                return RouteResult(intern_name=None, message=cleaned, is_hire=True)

        # Check "ask {name} to ..." pattern
        ask_match = re.match(r"^ask\s+(\w+)\s+to\s+(.+)$", cleaned, re.IGNORECASE | re.DOTALL)
        if ask_match and ask_match.group(1).lower() in self._names:
            return RouteResult(
                intern_name=ask_match.group(1).lower(),
                message=ask_match.group(2).strip(),
            )

        # Check "{name} ..." or "{name}, ..." pattern
        first_match = re.match(r"^(\w+)[,]?\s+(.+)$", cleaned, re.DOTALL)
        if first_match and first_match.group(1).lower() in self._names:
            return RouteResult(
                intern_name=first_match.group(1).lower(),
                message=first_match.group(2).strip(),
            )

        # Default: Jibsa orchestrator
        return RouteResult(intern_name=None, message=cleaned)
