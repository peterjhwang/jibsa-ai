"""
ToolRegistry — maps tool names to CrewAI tool instances.

Used to:
1. Build CrewAI tool lists per intern based on their JD
2. Check execution permissions before running plan steps
3. Provide tool descriptions for prompts and hire flow
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models.intern import InternJD

logger = logging.getLogger(__name__)

# Master catalog: tool name → metadata (description + valid write actions)
TOOL_CATALOG: dict[str, dict] = {
    "notion": {
        "description": "Query and manage tasks, projects, notes, journal entries, expenses, workouts in Notion",
        "write_actions": [
            "create_task", "update_task_status", "create_project",
            "create_note", "create_journal_entry", "log_expense", "log_workout",
        ],
    },
    "web_search": {
        "description": "Search the web for current information using DuckDuckGo",
        "write_actions": [],  # read-only tool
    },
    "code_exec": {
        "description": "Execute Python code in a sandboxed environment for calculations and data processing",
        "write_actions": [],  # read-only tool
    },
    "slack": {
        "description": "Post messages to Slack channels (requires approval)",
        "write_actions": ["post_message"],
    },
    "calendar": {
        "description": "View and manage Google Calendar events (requires Google connection)",
        "write_actions": ["create_event", "update_event", "delete_event"],
    },
    "gmail": {
        "description": "Read and send emails via Gmail (requires Google connection)",
        "write_actions": ["send_email", "reply_email", "create_draft"],
    },
    "drive": {
        "description": "Search, read, and create files in Google Drive (requires Google connection)",
        "write_actions": ["create_file", "upload_file"],
    },
    "file_gen": {
        "description": "Generate files (CSV, JSON, Markdown, text) and upload to Slack (requires approval)",
        "write_actions": ["upload_file"],
    },
    "image_gen": {
        "description": "Generate AI images using Nano Banana 2 (requires approval)",
        "write_actions": ["generate_image"],
    },
    "reminder": {
        "description": "Schedule reminders and timed messages (requires approval)",
        "write_actions": ["set_reminder"],
    },
    "web_reader": {
        "description": "Fetch and read full web page content using ZenRows (read-only)",
        "write_actions": [],  # read-only tool
    },
    "jira": {
        "description": "Search and manage Jira issues, transitions, comments, and worklogs",
        "write_actions": [
            "create_issue", "update_issue", "transition_issue", "add_comment", "add_worklog",
        ],
    },
    "confluence": {
        "description": "Search and manage Confluence pages and comments",
        "write_actions": [
            "create_page", "update_page", "add_comment",
        ],
    },
    "delegate": {
        "description": "Delegate a subtask to another intern and get their response",
        "write_actions": [],  # read-only tool
    },
}


class ToolRegistry:
    def __init__(self, catalog: dict[str, dict] | None = None):
        self._catalog = catalog or TOOL_CATALOG
        self._crewai_tools: dict[str, Any] = {}  # tool_name → CrewAI BaseTool instance

    def register_crewai_tool(self, name: str, tool_instance: Any) -> None:
        """Register a CrewAI tool instance by name."""
        self._crewai_tools[name] = tool_instance

    def get_all_tool_names(self) -> list[str]:
        return list(self._catalog.keys())

    def get_crewai_tools_for_intern(self, intern: InternJD) -> list:
        """Return list of CrewAI tool instances for an intern's allowed tools."""
        allowed = {t.lower() for t in intern.tools_allowed}
        return [
            self._crewai_tools[name]
            for name in allowed
            if name in self._crewai_tools
        ]

    def get_crewai_tools_for_jibsa(self) -> list:
        """Return all registered CrewAI tools (Jibsa has access to everything)."""
        return list(self._crewai_tools.values())

    def get_tools_for_intern(self, intern: InternJD) -> dict[str, dict]:
        """Return filtered subset of tool catalog based on intern's tools_allowed."""
        allowed = {t.lower() for t in intern.tools_allowed}
        return {
            name: info
            for name, info in self._catalog.items()
            if name.lower() in allowed
        }

    def get_tool_descriptions_for_prompt(self, intern: InternJD) -> str:
        """Format tool descriptions for display."""
        tools = self.get_tools_for_intern(intern)
        if not tools:
            return "No tools assigned. You can only have conversations."
        lines = []
        for name, info in tools.items():
            lines.append(f"- **{name}**: {info['description']}")
            if info.get("write_actions"):
                actions = ", ".join(info["write_actions"])
                lines.append(f"  Write actions (require approval): {actions}")
        return "\n".join(lines)

    def can_execute(self, intern: InternJD, service: str, action: str) -> bool:
        """Check if an intern is allowed to execute a specific write action."""
        allowed = {t.lower() for t in intern.tools_allowed}
        if service.lower() not in allowed:
            return False
        tool_info = self._catalog.get(service.lower())
        if not tool_info:
            return False
        # Read-only tools have no write_actions — always allowed
        if not tool_info["write_actions"]:
            return True
        return action in tool_info["write_actions"]

    def get_integration_names(self) -> list[str]:
        """Return all integration names from the catalog."""
        return list(self._catalog.keys())
