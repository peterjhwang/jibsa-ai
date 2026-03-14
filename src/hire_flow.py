"""
HireFlowManager — conversational JD creation for new interns.

Manages multi-turn conversations where the user creates a new intern
through natural dialogue. Uses CrewAI for the conversation.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .models.intern import InternJD

logger = logging.getLogger(__name__)


class HireFlowState(Enum):
    IDLE = "idle"
    GATHERING = "gathering"       # Claude is asking clarifying questions
    CONFIRMING = "confirming"     # JD drafted, waiting for approval


@dataclass
class HireSession:
    state: HireFlowState = HireFlowState.GATHERING
    thread_ts: str = ""
    user: str = ""
    conversation: list[dict] = field(default_factory=list)
    draft_jd: Optional[dict] = None


def _extract_intern_jd(text: str) -> dict | None:
    """Try to extract an intern_jd JSON from LLM output."""
    text = text.strip()

    # Try bare JSON
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if parsed.get("type") == "intern_jd":
                return parsed
        except json.JSONDecodeError:
            pass

    # Try ```json ... ``` block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if parsed.get("type") == "intern_jd":
                return parsed
        except json.JSONDecodeError:
            pass

    return None


def _validate_jd_data(jd_data: dict) -> list[str]:
    """Validate JD data before creating intern. Returns list of issues."""
    errors = []
    if not jd_data.get("name", "").strip():
        errors.append("Name is missing")
    if not jd_data.get("role", "").strip():
        errors.append("Role is missing")
    if not jd_data.get("responsibilities"):
        errors.append("At least one responsibility is required")
    # Validate using InternJD model
    try:
        intern = InternJD(
            name=jd_data.get("name", ""),
            role=jd_data.get("role", ""),
            responsibilities=jd_data.get("responsibilities", []),
            tone=jd_data.get("tone", ""),
            tools_allowed=jd_data.get("tools_allowed", []),
            autonomy_rules=jd_data.get("autonomy_rules", ""),
        )
        errors.extend(intern.validate())
    except Exception as e:
        errors.append(str(e))
    return errors


class HireFlowManager:
    def __init__(self, crew_runner: Any, intern_registry: Any, tool_registry: Any):
        """
        Args:
            crew_runner: CrewRunner instance
            intern_registry: InternRegistry instance
            tool_registry: ToolRegistry instance
        """
        self._runner = crew_runner
        self._registry = intern_registry
        self._tool_registry = tool_registry
        self._sessions: dict[str, HireSession] = {}

    def has_session(self, thread_ts: str) -> bool:
        return thread_ts in self._sessions

    def get_session(self, thread_ts: str) -> HireSession | None:
        return self._sessions.get(thread_ts)

    def start_session(self, thread_ts: str, user: str, initial_message: str) -> None:
        """Start a new hire flow session."""
        session = HireSession(
            state=HireFlowState.GATHERING,
            thread_ts=thread_ts,
            user=user,
            conversation=[{"role": "user", "content": initial_message}],
        )
        self._sessions[thread_ts] = session

    def handle(self, thread_ts: str, user: str, text: str) -> str:
        """
        Process a message in an active hire flow.
        Returns the response text to post in Slack.
        """
        session = self._sessions.get(thread_ts)
        if not session:
            return "No active hire session for this thread."

        # If we're in CONFIRMING state, check for approval/rejection
        if session.state == HireFlowState.CONFIRMING:
            return self._handle_confirmation(session, text)

        # Add user message to conversation
        session.conversation.append({"role": "user", "content": text})

        # Run via CrewAI
        available_tools = ", ".join(self._tool_registry.get_all_tool_names())
        response_text = self._runner.run_for_hire(
            user_message=text,
            available_tools=available_tools,
            history=session.conversation,
        )

        # Check if the response contains a complete JD
        jd_data = _extract_intern_jd(response_text)
        if jd_data:
            # Validate the JD
            validation_errors = _validate_jd_data(jd_data)
            if validation_errors:
                error_text = "\n".join(f"  - {e}" for e in validation_errors)
                session.conversation.append({"role": "assistant", "content": response_text})
                return (
                    f"Almost there, but the JD needs a few fixes:\n{error_text}\n\n"
                    f"Can you help me fill in the missing details?"
                )

            session.draft_jd = jd_data
            session.state = HireFlowState.CONFIRMING
            session.conversation.append({"role": "assistant", "content": response_text})
            return self._format_jd_confirmation(jd_data)

        # Still gathering info
        session.conversation.append({"role": "assistant", "content": response_text})
        return response_text

    def _handle_confirmation(self, session: HireSession, text: str) -> str:
        """Handle user response to a JD confirmation."""
        text_lower = text.lower().strip()

        if any(kw in text_lower for kw in ("yes", "approve", "go", "✅", "confirm", "looks good", "lgtm")):
            return self._create_intern(session)

        if any(kw in text_lower for kw in ("no", "cancel", "❌", "stop", "nevermind")):
            del self._sessions[session.thread_ts]
            return "Hire cancelled. Let me know if you want to try again."

        # Treat as a revision request — go back to GATHERING
        session.state = HireFlowState.GATHERING
        session.conversation.append({"role": "user", "content": text})
        return self.handle(session.thread_ts, session.user, text)

    def _create_intern(self, session: HireSession) -> str:
        """Create the intern from the draft JD."""
        jd_data = session.draft_jd
        if not jd_data:
            return "Something went wrong — no JD draft found."

        intern = InternJD(
            name=jd_data["name"],
            role=jd_data["role"],
            responsibilities=jd_data.get("responsibilities", []),
            tone=jd_data.get("tone", "Professional and helpful"),
            tools_allowed=jd_data.get("tools_allowed", ["notion"]),
            autonomy_rules=jd_data.get("autonomy_rules", "Always propose before acting"),
            created_by=session.user,
        )

        # Final validation
        errors = intern.validate()
        if errors:
            error_text = "\n".join(f"  - {e}" for e in errors)
            return f"⚠️ JD validation failed:\n{error_text}\n\nPlease fix and try again."

        result = self._registry.create_intern(intern)

        # Clean up session
        del self._sessions[session.thread_ts]

        if result.get("ok"):
            return (
                f"✅ *Intern '{intern.name}' is ready!*\n\n"
                f"You can now assign tasks: `@jibsa {intern.name.lower()} <your request>`"
            )
        else:
            return f"⚠️ Failed to create intern: {result.get('error', 'unknown error')}"

    def _format_jd_confirmation(self, jd: dict) -> str:
        """Format a JD draft for user confirmation."""
        responsibilities = "\n".join(f"  - {r}" for r in jd.get("responsibilities", []))
        tools = ", ".join(jd.get("tools_allowed", []))

        return (
            f"📋 *Here's the Job Description for your new intern:*\n\n"
            f"*Name:* {jd['name']}\n"
            f"*Role:* {jd['role']}\n"
            f"*Responsibilities:*\n{responsibilities}\n"
            f"*Tone:* {jd.get('tone', 'N/A')}\n"
            f"*Tools:* {tools}\n"
            f"*Autonomy Rules:* {jd.get('autonomy_rules', 'Always propose before acting')}\n\n"
            f"Does this look good? ✅ to confirm, or tell me what to change."
        )

    def cancel_session(self, thread_ts: str) -> None:
        """Clean up a hire session."""
        self._sessions.pop(thread_ts, None)
