"""
CalendarReadTool — CrewAI tool for reading Google Calendar events.

Uses per-user OAuth credentials via the current_user_id ContextVar.
Write operations (create/update/delete) go through the propose-approve flow.
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


class CalendarQueryInput(BaseModel):
    """Input schema for calendar queries."""
    query: str = Field(
        ...,
        description=(
            "What to look up (e.g. 'my meetings today', 'this week', "
            "'meetings with John', 'schedule a call Thursday 2pm')"
        ),
    )


class CalendarReadTool(BaseTool):
    name: str = "Google Calendar"
    description: str = (
        "Check your Google Calendar — view today's events, upcoming schedule, "
        "or search for specific events. To create, update, or delete events, "
        "propose an action plan instead. Requires the user to have connected Google."
    )
    args_schema: Type[BaseModel] = CalendarQueryInput
    google_oauth: object = None
    timezone: str = "UTC"

    def _run(self, query: str) -> str:
        user_id = current_user_id.get()
        if not user_id:
            return "Could not determine the requesting user."

        if not self.google_oauth:
            return "Google Calendar is not configured."

        creds = self.google_oauth.get_valid_credentials(user_id)
        if not creds:
            return (
                "You haven't connected Google yet. "
                "Say `connect google` to link your account."
            )

        try:
            from ..integrations.google_calendar_client import GoogleCalendarClient
            client = GoogleCalendarClient(creds)

            query_lower = query.lower()

            # Route by intent
            if any(kw in query_lower for kw in ("today", "today's", "today's")):
                events = client.list_today_events(self.timezone)
                return self._format_events(events, "Today's events")

            if any(kw in query_lower for kw in ("week", "upcoming", "next", "coming up")):
                events = client.list_upcoming_events(days=7, timezone=self.timezone)
                return self._format_events(events, "Upcoming events (7 days)")

            if any(kw in query_lower for kw in ("tomorrow",)):
                events = client.list_upcoming_events(days=2, timezone=self.timezone)
                return self._format_events(events, "Events in the next 2 days")

            # Default: search
            events = client.search_events(query, timezone=self.timezone)
            return self._format_events(events, f"Events matching '{query}'")

        except Exception as e:
            logger.warning("Calendar query failed for user %s: %s", user_id, e)
            return f"Calendar query failed: {e}"

    @staticmethod
    def _format_events(events: list[dict], header: str) -> str:
        if not events:
            return f"{header}: No events found."

        lines = [f"*{header}* ({len(events)} events):\n"]
        for event in events:
            summary = event.get("summary", "(no title)")
            start = event.get("start", {})
            start_time = start.get("dateTime", start.get("date", ""))
            # Simplify display
            if "T" in start_time:
                start_time = start_time.split("T")[1][:5]  # HH:MM
            end = event.get("end", {})
            end_time = end.get("dateTime", end.get("date", ""))
            if "T" in end_time:
                end_time = end_time.split("T")[1][:5]

            time_str = f"{start_time}–{end_time}" if start_time != end_time else start_time
            location = event.get("location", "")
            loc_str = f" | {location}" if location else ""

            lines.append(f"  - {time_str} {summary}{loc_str}")

        return "\n".join(lines)

    @classmethod
    def create(cls, google_oauth: GoogleOAuthManager, timezone: str = "UTC") -> CalendarReadTool:
        tool = cls()
        tool.google_oauth = google_oauth
        tool.timezone = timezone
        return tool
