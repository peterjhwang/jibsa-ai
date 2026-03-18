"""
NotionReadTool — CrewAI tool for read-only Notion queries.

This tool lets agents query Notion databases during reasoning.
Write operations are NOT exposed here — those go through the
propose-approve flow via action_plan JSON.

Supports per-user Notion OAuth: if the current user has connected their
own Notion workspace, uses their token. Otherwise falls back to the
global NOTION_TOKEN brain.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ..context import current_user_id

if TYPE_CHECKING:
    from ..integrations.notion_oauth import NotionOAuthManager
    from ..integrations.notion_second_brain import NotionSecondBrain
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
        "This is read-only — to create or update items, propose an action plan instead."
    )
    args_schema: Type[BaseModel] = NotionQueryInput
    notion_brain: object = None  # Global fallback brain
    notion_oauth: object = None  # NotionOAuthManager for per-user tokens
    notion_user_registry: object = None  # NotionUserRegistry for per-user DB registry
    notion_config: object = None  # Config dict for build_user_second_brain

    def _run(self, query: str) -> str:
        brain = self._resolve_brain()
        if not brain:
            return "Notion is not connected."

        try:
            context = brain.get_context_for_request(query)
            if not context:
                return f"No matching Notion data found for: {query}"
            return context
        except Exception as e:
            return f"Notion query failed: {e}"

    def _resolve_brain(self):
        """Resolve the NotionSecondBrain to use: per-user first, then global fallback."""
        user_id = current_user_id.get()

        # Try per-user brain if OAuth is configured
        if user_id and self.notion_oauth and self.notion_user_registry:
            token = self.notion_oauth.get_token(user_id)
            if token:
                try:
                    from ..integrations.notion_second_brain import build_user_second_brain
                    brain = build_user_second_brain(
                        user_id=user_id,
                        notion_oauth=self.notion_oauth,
                        user_registry=self.notion_user_registry,
                        config=self.notion_config or {},
                    )
                    if brain:
                        return brain
                except Exception as e:
                    logger.warning("Failed to build per-user Notion brain for %s: %s", user_id, e)

        # Fall back to global brain
        return self.notion_brain

    @classmethod
    def create(
        cls,
        notion_brain: NotionSecondBrain | None = None,
        notion_oauth: NotionOAuthManager | None = None,
        notion_user_registry: NotionUserRegistry | None = None,
        config: dict | None = None,
    ) -> NotionReadTool:
        """Factory method to create with proper references."""
        tool = cls()
        tool.notion_brain = notion_brain
        tool.notion_oauth = notion_oauth
        tool.notion_user_registry = notion_user_registry
        tool.notion_config = config
        return tool
