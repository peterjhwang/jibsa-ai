"""
GmailReadTool — CrewAI tool for reading Gmail messages.

Uses per-user OAuth credentials via the current_user_id ContextVar.
Write operations (send/reply/draft) go through the propose-approve flow.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ..context import current_user_id

if TYPE_CHECKING:
    from ..integrations.google_oauth import GoogleOAuthManager

logger = logging.getLogger(__name__)


class GmailQueryInput(BaseModel):
    """Input schema for Gmail queries."""
    query: str = Field(
        ...,
        description=(
            "Gmail search query (e.g. 'unread emails', 'from:boss@company.com', "
            "'subject:invoice', 'newer_than:1d')"
        ),
    )


class GmailReadTool(BaseTool):
    name: str = "Read Gmail"
    description: str = (
        "Search and read Gmail messages. Use Gmail search syntax "
        "(is:unread, from:someone, subject:topic, newer_than:1d). "
        "To send emails, reply, or create drafts, propose an action plan. "
        "Requires the user to have connected Google."
    )
    args_schema: Type[BaseModel] = GmailQueryInput
    google_oauth: object = None

    def _run(self, query: str) -> str:
        user_id = current_user_id.get()
        if not user_id:
            return "Could not determine the requesting user."

        if not self.google_oauth:
            return "Gmail is not configured."

        creds = self.google_oauth.get_valid_credentials(user_id)
        if not creds:
            return (
                "You haven't connected Google yet. "
                "Say `connect google` to link your account."
            )

        try:
            from ..integrations.gmail_client import GmailClient
            client = GmailClient(creds)

            query_lower = query.lower()

            # Map common intents to Gmail search syntax
            if query_lower in ("unread", "unread emails", "new emails"):
                gmail_query = "is:unread"
            elif query_lower in ("recent", "latest", "inbox"):
                gmail_query = ""
            else:
                gmail_query = query

            messages = client.list_messages(max_results=10, query=gmail_query)
            return self._format_messages(messages, query)

        except Exception as e:
            logger.warning("Gmail query failed for user %s: %s", user_id, e)
            return f"Gmail query failed: {e}"

    @staticmethod
    def _format_messages(messages: list[dict], query: str) -> str:
        if not messages:
            return f"No emails found for: {query}"

        lines = [f"*Gmail results* ({len(messages)} messages):\n"]
        for msg in messages:
            sender = msg.get("from", "Unknown")
            subject = msg.get("subject", "(no subject)")
            date = msg.get("date", "")
            snippet = msg.get("snippet", "")
            if len(snippet) > 100:
                snippet = snippet[:100] + "..."

            lines.append(f"  - *{subject}*\n    From: {sender} | {date}\n    {snippet}")

        return "\n".join(lines)

    @classmethod
    def create(cls, google_oauth: GoogleOAuthManager) -> GmailReadTool:
        tool = cls()
        tool.google_oauth = google_oauth
        return tool
