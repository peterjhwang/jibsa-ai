"""
NotionReadTool — CrewAI tool for read-only Notion queries.

This tool lets agents query Notion databases during reasoning.
Write operations are NOT exposed here — those go through the
propose-approve flow via action_plan JSON.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..integrations.notion_second_brain import NotionSecondBrain


class NotionQueryInput(BaseModel):
    """Input schema for Notion queries."""
    query: str = Field(..., description="What to search for in Notion (e.g. 'my tasks', 'projects in progress', 'recent expenses')")


class NotionReadTool(BaseTool):
    name: str = "Query Notion"
    description: str = (
        "Search the user's Notion workspace for information. "
        "Use this to look up tasks, projects, notes, expenses, workouts, contacts, and more. "
        "This is read-only — to create or update items, propose an action plan instead."
    )
    args_schema: Type[BaseModel] = NotionQueryInput
    notion_brain: object = None  # Set after init to avoid pydantic issues

    def _run(self, query: str) -> str:
        if not self.notion_brain:
            return "Notion is not connected."

        try:
            context = self.notion_brain.get_context_for_request(query)
            if not context:
                return f"No matching Notion data found for: {query}"
            return context
        except Exception as e:
            return f"Notion query failed: {e}"

    @classmethod
    def create(cls, notion_brain: NotionSecondBrain) -> NotionReadTool:
        """Factory method to create with proper Notion brain reference."""
        tool = cls()
        tool.notion_brain = notion_brain
        return tool
