"""
NotionReadTool — CrewAI tool for read-only Notion queries.

This tool lets agents query Notion databases during reasoning.
Write operations are NOT exposed here — those go through the
propose-approve flow via action_plan JSON.

Uses per-user Notion OAuth: each user connects their own workspace
via `connect notion`. Requires the user to have connected.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ..context import current_user_id
from ..integrations.notion_second_brain import build_user_second_brain

if TYPE_CHECKING:
    from ..integrations.notion_oauth import NotionOAuthManager
    from ..integrations.notion_user_registry import NotionUserRegistry

logger = logging.getLogger(__name__)


class NotionQueryInput(BaseModel):
    """Input schema for Notion queries."""
    query: str = Field(..., description="What to search for in Notion (e.g. 'my tasks', 'projects in progress', 'recent expenses')")


class NotionReadTool(BaseTool):
    name: str = "Query Notion"
    description: str = (
        "Search the user's Notion workspace for information. "
        "Use this to look up tasks, projects, notes, expenses, workouts, contacts, and more. "
        "This is read-only — to create or update items, propose an action plan instead. "
        "Requires the user to have connected Notion via `connect notion`."
    )
    args_schema: Type[BaseModel] = NotionQueryInput
    notion_oauth: object = None  # NotionOAuthManager for per-user tokens
    notion_user_registry: object = None  # NotionUserRegistry for per-user DB registry
    notion_config: object = None  # Config dict for build_user_second_brain

    def _run(self, query: str) -> str:
        user_id = current_user_id.get()
        if not user_id:
            return "Could not determine the requesting user."

        if not self.notion_oauth or not self.notion_user_registry:
            return "Notion is not configured."

        token = self.notion_oauth.get_token(user_id)
        if not token:
            return (
                "You haven't connected Notion yet. "
                "Say `connect notion` to link your workspace."
            )

        try:
            brain = build_user_second_brain(
                user_id=user_id,
                notion_oauth=self.notion_oauth,
                user_registry=self.notion_user_registry,
                config=self.notion_config or {},
            )
            if not brain:
                return "Could not initialize Notion connection."

            context = brain.get_context_for_request(query)
            if not context:
                return f"No matching Notion data found for: {query}"
            return context
        except Exception as e:
            logger.warning("Notion query failed for user %s: %s", user_id, e)
            return f"Notion query failed: {e}"

    @classmethod
    def create(
        cls,
        notion_oauth: NotionOAuthManager | None = None,
        notion_user_registry: NotionUserRegistry | None = None,
        config: dict | None = None,
    ) -> NotionReadTool:
        """Factory method to create with proper references."""
        tool = cls()
        tool.notion_oauth = notion_oauth
        tool.notion_user_registry = notion_user_registry
        tool.notion_config = config
        return tool
