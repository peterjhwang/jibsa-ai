"""
HireFlowManager — conversational JD creation for new interns.

Manages multi-turn conversations where the user creates a new intern
through natural dialogue. Claude helps refine the Job Description.
"""
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"


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
    import re
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if parsed.get("type") == "intern_jd":
                return parsed
        except json.JSONDecodeError:
            pass

    return None


class HireFlowManager:
    def __init__(self, llm_runner: Any, intern_registry: Any, tool_registry: Any):
        """
        Args:
            llm_runner: LLMRunner instance
            intern_registry: InternRegistry instance
            tool_registry: ToolRegistry instance
        """
        self._runner = llm_runner
        self._registry = intern_registry
        self._tool_registry = tool_registry
        self._sessions: dict[str, HireSession] = {}  # thread_ts → session

        # Load hire prompt template
        try:
            with open(_CONFIG_DIR / "prompts" / "hire.txt") as f:
                self._prompt_template = f.read()
        except FileNotFoundError:
            logger.warning("hire.txt not found — using fallback prompt")
            self._prompt_template = (
                "You are helping create a new AI intern. "
                "Gather: name, role, responsibilities, tone, tools_allowed, autonomy_rules. "
                "When complete, output JSON with type: intern_jd.\n\n{history}"
            )

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

        # Build hire prompt with conversation history
        available_tools = ", ".join(self._tool_registry.get_all_tool_names())
        history_text = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Jibsa'}: {m['content']}"
            for m in session.conversation
        )

        from datetime import datetime
        now = datetime.now()

        response = self._runner.run(
            user_message=text,
            extra_replacements={
                "{available_tools}": available_tools,
                "{history}": history_text,
                "{date}": now.strftime("%A, %B %d, %Y"),
                "{time}": now.strftime("%H:%M"),
            },
        )

        if isinstance(response, dict):
            # LLM returned a structured response (unlikely in hire flow)
            response_text = json.dumps(response, indent=2)
        else:
            response_text = str(response)

        # Check if the response contains a complete JD
        jd_data = _extract_intern_jd(response_text)
        if jd_data:
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

        from .models.intern import InternJD
        intern = InternJD(
            name=jd_data["name"],
            role=jd_data["role"],
            responsibilities=jd_data.get("responsibilities", []),
            tone=jd_data.get("tone", "Professional and helpful"),
            tools_allowed=jd_data.get("tools_allowed", ["notion"]),
            autonomy_rules=jd_data.get("autonomy_rules", "Always propose before acting"),
            created_by=session.user,
        )

        result = self._registry.create_intern(intern)

        # Clean up session
        del self._sessions[session.thread_ts]

        if result.get("ok"):
            url = result.get("url", "")
            url_text = f" (<{url}|view in Notion>)" if url else ""
            return (
                f"✅ *Intern '{intern.name}' is ready!*{url_text}\n\n"
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
