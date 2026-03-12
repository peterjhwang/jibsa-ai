"""
ToolRegistry — maps tool names to integration capabilities.

Used to:
1. Filter available tools per intern based on their JD
2. Build tool descriptions for system prompts
3. Check execution permissions before running plan steps
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models.intern import InternJD

logger = logging.getLogger(__name__)

# Master catalog of available tools and their actions
TOOL_CATALOG: dict[str, dict] = {
    "notion": {
        "description": "Create/update tasks, projects, notes, journal entries, expenses, workouts in Notion",
        "actions": [
            "create_task", "update_task_status", "create_project",
            "create_note", "create_journal_entry", "log_expense", "log_workout",
        ],
    },
    # Future tools — add here as they become available
    # "web_search": {
    #     "description": "Search the web for information",
    #     "actions": ["search"],
    # },
    # "slack": {
    #     "description": "Post messages in Slack channels",
    #     "actions": ["post_message"],
    # },
}


class ToolRegistry:
    def __init__(self, catalog: dict[str, dict] | None = None):
        self._catalog = catalog or TOOL_CATALOG

    def get_all_tool_names(self) -> list[str]:
        return list(self._catalog.keys())

    def get_tools_for_intern(self, intern: InternJD) -> dict[str, dict]:
        """Return filtered subset of tool catalog based on intern's tools_allowed."""
        allowed = {t.lower() for t in intern.tools_allowed}
        return {
            name: info
            for name, info in self._catalog.items()
            if name.lower() in allowed
        }

    def get_tool_descriptions_for_prompt(self, intern: InternJD) -> str:
        """Format tool descriptions for injection into system prompts."""
        tools = self.get_tools_for_intern(intern)
        if not tools:
            return "No tools assigned. You can only have conversations."
        lines = []
        for name, info in tools.items():
            actions = ", ".join(info["actions"])
            lines.append(f"- **{name}**: {info['description']}\n  Actions: {actions}")
        return "\n".join(lines)

    def can_execute(self, intern: InternJD, service: str, action: str) -> bool:
        """Check if an intern is allowed to execute a specific service action."""
        allowed = {t.lower() for t in intern.tools_allowed}
        if service.lower() not in allowed:
            return False
        tool_info = self._catalog.get(service.lower())
        if not tool_info:
            return False
        return action in tool_info["actions"]

    def get_integration_names(self) -> list[str]:
        """Return all integration names from the catalog (for active_integrations)."""
        return list(self._catalog.keys())
