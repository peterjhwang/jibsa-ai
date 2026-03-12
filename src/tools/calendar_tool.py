"""
CalendarTool — CrewAI tool stub for Google Calendar integration.

This is a placeholder for Phase 3. Currently returns helpful messages
explaining the feature is coming soon.
"""
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class CalendarInput(BaseModel):
    """Input schema for calendar queries."""
    query: str = Field(..., description="What to look up or schedule (e.g. 'my meetings today', 'schedule a call Thursday 2pm')")


class CalendarTool(BaseTool):
    name: str = "Google Calendar"
    description: str = (
        "Check your schedule, list upcoming events, or propose new calendar entries. "
        "Note: This integration is in preview — read operations show sample data, "
        "write operations will be available in a future update."
    )
    args_schema: Type[BaseModel] = CalendarInput

    def _run(self, query: str) -> str:
        query_lower = query.lower()

        # Detect read vs write intent
        write_keywords = ("schedule", "create", "add", "book", "set up", "move", "cancel", "reschedule")
        is_write = any(kw in query_lower for kw in write_keywords)

        if is_write:
            return (
                "Calendar write operations (create/update/delete events) are coming in Phase 3. "
                "For now, I can only tell you about the calendar integration roadmap:\n"
                "- Google Calendar read access (view events, check availability)\n"
                "- Event creation with approval flow\n"
                "- Morning briefing with today's schedule\n\n"
                "To schedule something now, you could create a Notion task with a due date instead."
            )

        return (
            "Google Calendar integration is coming in Phase 3. "
            "Once connected, I'll be able to:\n"
            "- Show your upcoming meetings and events\n"
            "- Check your availability for scheduling\n"
            "- Create calendar events (with approval)\n"
            "- Send morning briefings with your daily schedule\n\n"
            "For now, check your calendar app directly, or ask me to create a Notion task with a due date."
        )
