"""
MessageRouter — parses incoming Slack messages to determine the target intern.

Patterns:
  "@jibsa alex do X"        → ("alex", "do X")
  "@jibsa ask alex to do X" → ("alex", "do X")
  "@jibsa hire ..."         → (None, "hire ...", True)
  "@jibsa list interns"     → (None, "list interns", False)
  "@jibsa show alex's jd"   → (None, "show alex's jd", False)
  "@jibsa fire alex"        → (None, "fire alex", False)
  "@jibsa do X"             → (None, "do X", False)
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

HIRE_KEYWORDS = {"hire", "create intern", "new intern", "add intern"}


def _parse_team_request(text: str, known_names: set[str]) -> tuple[list[str], str]:
    """Parse 'alex, sarah to do something' into ([names], task).

    Handles:
      - "alex, sarah to ..."
      - "alex and sarah to ..."
      - "alex, sarah, bob to ..."
    """
    # Split on "to " to separate names from task
    parts = re.split(r"\s+to\s+", text, maxsplit=1)
    if len(parts) != 2:
        return [], text

    names_str, task = parts
    # Parse names: split on comma, "and", or whitespace
    raw_names = re.split(r"[,\s]+(?:and\s+)?|(?:\s+and\s+)", names_str.strip())
    names = [n.strip().lower() for n in raw_names if n.strip().lower() in known_names]

    if len(names) < 2:
        return [], text

    return names, task.strip()


@dataclass
class RouteResult:
    intern_name: Optional[str]  # None = Jibsa orchestrator
    message: str                # cleaned message for the target
    is_hire: bool = False       # hire flow trigger
    is_team: bool = False       # multi-intern team request
    team_names: list[str] = field(default_factory=list)


class MessageRouter:
    def __init__(self, known_names: list[str]):
        """known_names: list of active intern names (case-insensitive lookup)."""
        self._names: set[str] = {n.lower() for n in known_names}

    def update_names(self, names: list[str]) -> None:
        """Update known intern names (e.g. after a hire)."""
        self._names = {n.lower() for n in names}

    def route(self, text: str) -> RouteResult:
        """Parse a message and return routing info."""
        cleaned = re.sub(r"^<@\w+>\s*", "", text).strip()
        cleaned_lower = cleaned.lower()

        # Check for hire intent
        for kw in HIRE_KEYWORDS:
            if cleaned_lower.startswith(kw):
                return RouteResult(intern_name=None, message=cleaned, is_hire=True)

        # Check for "form team" intent
        if cleaned_lower.startswith("form team"):
            team_text = cleaned[len("form team"):].strip()
            names, task = _parse_team_request(team_text, self._names)
            if names:
                return RouteResult(
                    intern_name=None, message=task,
                    is_team=True, team_names=names,
                )

        # Management commands are passed through to orchestrator (not routed to interns)
        if cleaned_lower in ("list interns", "team", "interns", "show team", "history"):
            return RouteResult(intern_name=None, message=cleaned)

        # Help command (with optional target: "help", "help alex")
        if cleaned_lower == "help" or cleaned_lower.startswith("help "):
            return RouteResult(intern_name=None, message=cleaned)

        if cleaned_lower.startswith("show ") or cleaned_lower.startswith("fire "):
            return RouteResult(intern_name=None, message=cleaned)

        # Edit JD: "edit alex's jd", "edit alex"
        if cleaned_lower.startswith("edit "):
            return RouteResult(intern_name=None, message=cleaned)

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
