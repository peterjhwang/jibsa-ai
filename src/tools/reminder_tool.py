"""
ReminderTool — CrewAI tool for scheduling reminders.

This is a write tool — the agent proposes a reminder, the user approves,
and the orchestrator schedules it via APScheduler.
"""
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class ReminderInput(BaseModel):
    """Input schema for reminders."""
    message: str = Field(..., description="The reminder message (what to remind about)")
    when: str = Field(..., description="When to send the reminder. Use ISO 8601 format (e.g. '2026-03-14T09:00:00') or relative like 'in 30 minutes', 'in 2 hours', 'tomorrow at 9am'")


class ReminderTool(BaseTool):
    name: str = "Set Reminder"
    description: str = (
        "Schedule a reminder message for a specific time. "
        "This requires approval — the reminder will be proposed first, then scheduled after confirmation. "
        "The reminder will be posted as a Slack message in the current thread when the time comes."
    )
    args_schema: Type[BaseModel] = ReminderInput

    def _run(self, message: str, when: str) -> str:
        short_msg = message[:60] + "..." if len(message) > 60 else message
        return (
            f"To schedule this reminder, propose an action plan with:\n"
            f'{{"type": "action_plan", "summary": "Set reminder: {short_msg}", '
            f'"steps": [{{"service": "reminder", "action": "set_reminder", '
            f'"params": {{"message": "{message}", "when": "{when}"}}, '
            f'"description": "Remind: {short_msg} at {when}"}}], '
            f'"needs_approval": true}}'
        )
