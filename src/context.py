"""
Context variables for threading per-request state through CrewAI execution.

CrewAI tools need access to the requesting user's identity (e.g. to fetch
their personal OAuth tokens). Since tools don't receive user info directly,
we use contextvars to pass it through the execution stack.
"""
from contextvars import ContextVar

# The Slack user ID of the user who sent the current request.
# Set by the orchestrator before crew.kickoff(), read by tools.
current_user_id: ContextVar[str] = ContextVar("current_user_id", default="")
