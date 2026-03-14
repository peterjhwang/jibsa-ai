"""
SOPFlowManager — conversational SOP creation.

Manages multi-turn conversations where the user creates a new SOP
through natural dialogue. Follows the same pattern as HireFlowManager.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .models.sop import SOP

logger = logging.getLogger(__name__)


class SOPFlowState(Enum):
    IDLE = "idle"
    GATHERING = "gathering"
    CONFIRMING = "confirming"


@dataclass
class SOPSession:
    state: SOPFlowState = SOPFlowState.GATHERING
    thread_ts: str = ""
    user: str = ""
    intern_name: str | None = None
    conversation: list[dict] = field(default_factory=list)
    draft_sop: Optional[dict] = None


def _extract_sop_json(text: str) -> dict | None:
    """Try to extract a SOP JSON from LLM output."""
    text = text.strip()

    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if parsed.get("type") == "sop":
                return parsed
        except json.JSONDecodeError:
            pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if parsed.get("type") == "sop":
                return parsed
        except json.JSONDecodeError:
            pass

    return None


def _validate_sop_data(sop_data: dict) -> list[str]:
    """Validate SOP data before creating. Returns list of issues."""
    try:
        sop = SOP(
            name=sop_data.get("name", ""),
            trigger_keywords=sop_data.get("trigger_keywords", []),
            description=sop_data.get("description", ""),
            steps=sop_data.get("steps", []),
            expected_output=sop_data.get("expected_output", ""),
            tools_required=sop_data.get("tools_required", []),
            approval_required=sop_data.get("approval_required", True),
            priority=sop_data.get("priority", 0),
        )
        return sop.validate()
    except Exception as e:
        return [str(e)]


class SOPFlowManager:
    def __init__(self, crew_runner: Any, sop_registry: Any, tool_registry: Any):
        self._runner = crew_runner
        self._sop_registry = sop_registry
        self._tool_registry = tool_registry
        self._sessions: dict[str, SOPSession] = {}

    def has_session(self, thread_ts: str) -> bool:
        return thread_ts in self._sessions

    def get_session(self, thread_ts: str) -> SOPSession | None:
        return self._sessions.get(thread_ts)

    def start_session(
        self, thread_ts: str, user: str, initial_message: str,
        intern_name: str | None = None,
    ) -> None:
        """Start a new SOP creation session."""
        session = SOPSession(
            state=SOPFlowState.GATHERING,
            thread_ts=thread_ts,
            user=user,
            intern_name=intern_name,
            conversation=[{"role": "user", "content": initial_message}],
        )
        self._sessions[thread_ts] = session

    def handle(self, thread_ts: str, user: str, text: str) -> str:
        """Process a message in an active SOP flow. Returns response text."""
        session = self._sessions.get(thread_ts)
        if not session:
            return "No active SOP creation session for this thread."

        if session.state == SOPFlowState.CONFIRMING:
            return self._handle_confirmation(session, text)

        session.conversation.append({"role": "user", "content": text})

        available_tools = ", ".join(self._tool_registry.get_all_tool_names())
        response_text = self._runner.run_for_sop_creation(
            user_message=text,
            available_tools=available_tools,
            intern_name=session.intern_name,
            history=session.conversation,
        )

        sop_data = _extract_sop_json(response_text)
        if sop_data:
            validation_errors = _validate_sop_data(sop_data)
            if validation_errors:
                error_text = "\n".join(f"  - {e}" for e in validation_errors)
                session.conversation.append({"role": "assistant", "content": response_text})
                return (
                    f"Almost there, but the SOP needs a few fixes:\n{error_text}\n\n"
                    f"Can you help me fill in the missing details?"
                )

            sop_data["intern_id"] = session.intern_name
            session.draft_sop = sop_data
            session.state = SOPFlowState.CONFIRMING
            session.conversation.append({"role": "assistant", "content": response_text})
            return self._format_sop_confirmation(sop_data)

        session.conversation.append({"role": "assistant", "content": response_text})
        return response_text

    def _handle_confirmation(self, session: SOPSession, text: str) -> str:
        text_lower = text.lower().strip()

        if any(kw in text_lower for kw in ("yes", "approve", "go", "✅", "confirm", "looks good", "lgtm")):
            return self._create_sop(session)

        if any(kw in text_lower for kw in ("no", "cancel", "❌", "stop", "nevermind")):
            del self._sessions[session.thread_ts]
            return "SOP creation cancelled. Let me know if you want to try again."

        session.state = SOPFlowState.GATHERING
        session.conversation.append({"role": "user", "content": text})
        return self.handle(session.thread_ts, session.user, text)

    def _create_sop(self, session: SOPSession) -> str:
        sop_data = session.draft_sop
        if not sop_data:
            return "Something went wrong — no SOP draft found."

        sop = SOP(
            name=sop_data["name"],
            intern_id=sop_data.get("intern_id"),
            trigger_keywords=sop_data.get("trigger_keywords", []),
            description=sop_data.get("description", ""),
            steps=sop_data.get("steps", []),
            expected_output=sop_data.get("expected_output", ""),
            tools_required=sop_data.get("tools_required", []),
            approval_required=sop_data.get("approval_required", True),
            priority=sop_data.get("priority", 0),
        )

        errors = sop.validate()
        if errors:
            error_text = "\n".join(f"  - {e}" for e in errors)
            return f"⚠️ SOP validation failed:\n{error_text}\n\nPlease fix and try again."

        result = self._sop_registry.create_sop(sop)

        del self._sessions[session.thread_ts]

        if result.get("ok"):
            scope = f"for intern *{sop.intern_id}*" if sop.intern_id else "(shared)"
            return (
                f"✅ *SOP '{sop.name}' created {scope}!*\n\n"
                f"Triggers: {', '.join(sop.trigger_keywords)}\n"
                f"It will activate when matching keywords appear in messages."
            )
        else:
            return f"⚠️ Failed to create SOP: {result.get('error', 'unknown error')}"

    def _format_sop_confirmation(self, sop: dict) -> str:
        steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(sop.get("steps", [])))
        keywords = ", ".join(sop.get("trigger_keywords", []))
        tools = ", ".join(sop.get("tools_required", [])) or "none"
        scope = f"Intern: {sop.get('intern_id')}" if sop.get("intern_id") else "Shared (all interns)"

        return (
            f"📋 *Here's the SOP you're creating:*\n\n"
            f"*Name:* {sop['name']}\n"
            f"*Scope:* {scope}\n"
            f"*Trigger Keywords:* {keywords}\n"
            f"*Description:* {sop.get('description', '')}\n"
            f"*Steps:*\n{steps}\n"
            f"*Expected Output:* {sop.get('expected_output', '')}\n"
            f"*Tools:* {tools}\n"
            f"*Approval Required:* {'Yes' if sop.get('approval_required', True) else 'No'}\n"
            f"*Priority:* {sop.get('priority', 0)}\n\n"
            f"Does this look good? ✅ to confirm, or tell me what to change."
        )

    def cancel_session(self, thread_ts: str) -> None:
        self._sessions.pop(thread_ts, None)
