"""
SlackTool — CrewAI tool for posting messages in Slack.

This is a write tool — actions go through the propose-approve flow.
The tool itself doesn't post directly; the orchestrator executes after approval.
"""
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class SlackPostInput(BaseModel):
    """Input schema for Slack posting."""
    channel: str = Field(..., description="Slack channel name or ID to post to (e.g. '#general', 'C0123ABC')")
    message: str = Field(..., description="The message text to post")


class SlackTool(BaseTool):
    name: str = "Post Slack Message"
    description: str = (
        "Post a message to a Slack channel. "
        "This requires approval — the message will be proposed first, then sent after confirmation. "
        "Provide the channel name (e.g. '#general') and the message text."
    )
    args_schema: Type[BaseModel] = SlackPostInput

    def _run(self, channel: str, message: str) -> str:
        # This tool doesn't execute directly — it returns instructions
        # for the agent to propose an action plan instead.
        return (
            f"To post to Slack, propose an action plan with:\n"
            f'{{"type": "action_plan", "summary": "Post message to {channel}", '
            f'"steps": [{{"service": "slack", "action": "post_message", '
            f'"params": {{"channel": "{channel}", "message": "{message}"}}, '
            f'"description": "Post message to {channel}"}}], '
            f'"needs_approval": true}}'
        )
