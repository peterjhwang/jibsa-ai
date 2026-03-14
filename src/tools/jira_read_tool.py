"""
JiraReadTool — CrewAI tool for read-only Jira queries.

This tool lets agents query Jira issues during reasoning.
Write operations are NOT exposed here — those go through the
propose-approve flow via action_plan JSON.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass  # Jira client injected as plain object


_ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


class JiraQueryInput(BaseModel):
    """Input schema for Jira queries."""

    query: str = Field(
        ...,
        description=(
            "Jira search query — use an issue key (e.g. PROJ-123) to get "
            "details, or describe what you're looking for (e.g. 'open bugs "
            "in PROJECT')"
        ),
    )


class JiraReadTool(BaseTool):
    name: str = "Search Jira"
    description: str = (
        "Search Jira for issues, epics, and project data. "
        "Provide an issue key like PROJ-123 to get details, or a natural "
        "language query to search. This is read-only — to create or update "
        "issues, propose an action plan instead."
    )
    args_schema: Type[BaseModel] = JiraQueryInput
    jira_client: object = None  # Set after init to avoid pydantic issues

    def _run(self, query: str) -> str:
        if self.jira_client is None:
            return "Jira is not connected."

        try:
            if _ISSUE_KEY_RE.match(query.strip()):
                return self._format_issue(
                    self.jira_client.get_issue(query.strip())
                )
            # Treat query as JQL and pass through directly
            results = self.jira_client.search_issues(query)
            return self._format_search_results(results)
        except Exception as e:
            return f"Jira query failed: {e}"

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_issue(issue: dict) -> str:
        """Format a single Jira issue with full details."""
        fields = issue.get("fields", {})
        key = issue.get("key", "?")
        summary = fields.get("summary", "")
        status = (fields.get("status") or {}).get("name", "Unknown")
        assignee_obj = fields.get("assignee") or {}
        assignee = assignee_obj.get("displayName", "Unassigned")
        priority = (fields.get("priority") or {}).get("name", "None")
        description = fields.get("description", "") or ""
        if len(description) > 500:
            description = description[:500] + "..."

        lines = [
            f"[{key}] {summary}",
            f"  Status: {status}",
            f"  Assignee: {assignee}",
            f"  Priority: {priority}",
        ]
        if description:
            lines.append(f"  Description: {description}")
        return "\n".join(lines)

    @staticmethod
    def _format_search_results(issues: list[dict]) -> str:
        """Format a list of Jira issues as compact one-liners."""
        if not issues:
            return "No issues found."

        lines: list[str] = []
        for issue in issues:
            fields = issue.get("fields", {})
            key = issue.get("key", "?")
            summary = fields.get("summary", "")
            status = (fields.get("status") or {}).get("name", "Unknown")
            assignee_obj = fields.get("assignee") or {}
            assignee = assignee_obj.get("displayName", "Unassigned")
            lines.append(
                f"[{key}] {summary} | Status: {status} | Assignee: {assignee}"
            )
        return "\n".join(lines)

    @classmethod
    def create(cls, jira_client: object) -> JiraReadTool:
        """Factory method to create with proper Jira client reference."""
        tool = cls()
        tool.jira_client = jira_client
        return tool
