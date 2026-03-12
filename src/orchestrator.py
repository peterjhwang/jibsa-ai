"""
Orchestrator — the central router.

Receives messages from Slack, manages conversation history per thread,
routes to the correct intern (or Jibsa itself), runs CrewAI crews,
handles the propose-approve flow, and dispatches approved plans to
integration clients.

v0.6: Full CrewAI integration — each intern is a CrewAI Agent with
native tool use, memory, and multi-provider LLM support.
"""
import logging
from pathlib import Path
from typing import Any

from .approval import ApprovalManager, ApprovalState
from .crew_runner import CrewRunner
from .hire_flow import HireFlowManager
from .intern_registry import InternRegistry
from .integrations.notion_second_brain import build_second_brain
from .models.intern import InternJD
from .router import MessageRouter, RouteResult
from .tool_registry import ToolRegistry
from .tools.notion_read_tool import NotionReadTool
from .tools.web_search_tool import WebSearchTool
from .tools.code_exec_tool import CodeExecTool

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"

# Commands handled directly by the orchestrator (not routed to interns/LLM)
MANAGEMENT_COMMANDS = {
    "list interns", "team", "interns", "show team",
}


def _active_integrations(config: dict) -> list[str]:
    """Return list of enabled integration names from config."""
    return [
        name
        for name, cfg in config.get("integrations", {}).items()
        if cfg.get("enabled", False)
    ]


class Orchestrator:
    def __init__(self, slack_client: Any, config: dict):
        self.slack = slack_client
        self.config = config
        self.notion = build_second_brain(config)
        self.approval = ApprovalManager(config)

        # CrewAI runner
        self.runner = CrewRunner(config)

        # Tool registry with CrewAI tool instances
        self.tool_registry = ToolRegistry()
        self._register_crewai_tools()

        # Intern management
        self.intern_registry = InternRegistry(self.notion, config)
        self.router = MessageRouter(self.intern_registry.get_intern_names())

        # Hire flow
        self.hire_flow = HireFlowManager(self.runner, self.intern_registry, self.tool_registry)

        self._history: dict[str, list[dict]] = {}
        self._max_history: int = config.get("jibsa", {}).get("max_history", 20)

        # Track which intern is active per thread (for approval context)
        self._thread_intern: dict[str, str | None] = {}

    def _register_crewai_tools(self) -> None:
        """Create and register CrewAI tool instances."""
        # Notion read tool (available if Notion is connected)
        if self.notion:
            self.tool_registry.register_crewai_tool(
                "notion", NotionReadTool.create(self.notion)
            )

        # Web search tool (always available)
        self.tool_registry.register_crewai_tool("web_search", WebSearchTool())

        # Code execution tool (always available)
        self.tool_registry.register_crewai_tool("code_exec", CodeExecTool())

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def handle_message(
        self,
        channel: str,
        thread_ts: str,
        user: str,
        text: str,
    ) -> None:
        """Process an incoming Slack message."""
        # Priority 1: Active hire flow session
        if self.hire_flow.has_session(thread_ts):
            response = self.hire_flow.handle(thread_ts, user, text)
            self._post(channel, thread_ts, response)
            self.router.update_names(self.intern_registry.get_intern_names())
            return

        # Priority 2: Pending approval
        ctx = self.approval.get(thread_ts)
        if ctx.state == ApprovalState.PENDING:
            self._handle_approval_response(ctx, channel, thread_ts, text)
            return

        # Priority 3: Route the message
        route = self.router.route(text)

        # Management commands
        if route.message.lower().strip() in MANAGEMENT_COMMANDS:
            self._handle_list_interns(channel, thread_ts)
            return

        # Show intern JD: "show alex's jd" or "show alex"
        if route.message.lower().startswith("show ") and not route.intern_name:
            intern_name = route.message[5:].strip().rstrip("'s jd").rstrip("'s").strip()
            intern = self.intern_registry.get_intern(intern_name)
            if intern:
                self._post(channel, thread_ts, f"📋 *{intern.name}'s Job Description:*\n\n{intern.format_jd()}")
                return

        # Fire intern: "fire alex"
        if route.message.lower().startswith("fire ") and not route.intern_name:
            intern_name = route.message[5:].strip()
            self._handle_fire_intern(channel, thread_ts, intern_name)
            return

        if route.is_hire:
            self._start_hire_flow(channel, thread_ts, user, route.message)
            return

        if route.intern_name:
            intern = self.intern_registry.get_intern(route.intern_name)
            if intern:
                self._handle_intern_request(channel, thread_ts, user, route.message, intern)
            else:
                self._post(
                    channel, thread_ts,
                    f"I don't have an intern named '{route.intern_name}'. "
                    f"Available interns: {', '.join(self.intern_registry.get_intern_names()) or 'none yet'}. "
                    f"Say `hire a <role> intern` to create one.",
                )
            return

        # Default: Jibsa orchestrator handles it
        self._handle_new_request(channel, thread_ts, user, route.message)

    # ------------------------------------------------------------------
    # Management commands
    # ------------------------------------------------------------------

    def _handle_list_interns(self, channel: str, thread_ts: str) -> None:
        """List all active interns."""
        interns = self.intern_registry.list_interns(force_refresh=True)
        if not interns:
            self._post(channel, thread_ts,
                       "No interns yet. Say `hire a <role> intern` to create one.")
            return

        lines = ["*Your AI Interns:*\n"]
        for i in interns:
            tools = ", ".join(i.tools_allowed) or "none"
            lines.append(f"  *{i.name}* — {i.role} (tools: {tools})")
        lines.append(f"\n_{len(interns)} active intern{'s' if len(interns) != 1 else ''}_")
        self._post(channel, thread_ts, "\n".join(lines))

    def _handle_fire_intern(self, channel: str, thread_ts: str, name: str) -> None:
        """Deactivate an intern."""
        result = self.intern_registry.deactivate_intern(name)
        if result.get("ok"):
            self.router.update_names(self.intern_registry.get_intern_names())
            self._post(channel, thread_ts, f"✅ Intern '{name}' has been deactivated.")
        else:
            self._post(channel, thread_ts, f"⚠️ {result.get('error', 'Could not fire intern')}")

    # ------------------------------------------------------------------
    # Hire flow
    # ------------------------------------------------------------------

    def _start_hire_flow(self, channel: str, thread_ts: str, user: str, text: str) -> None:
        """Start a conversational hire flow."""
        self.hire_flow.start_session(thread_ts, user, text)
        response = self.hire_flow.handle(thread_ts, user, text)
        self._post(channel, thread_ts, response)

    # ------------------------------------------------------------------
    # Intern request handling (CrewAI)
    # ------------------------------------------------------------------

    def _handle_intern_request(
        self,
        channel: str,
        thread_ts: str,
        user: str,
        text: str,
        intern: InternJD,
    ) -> None:
        """Handle a request routed to a specific intern via CrewAI."""
        history = self._get_history(thread_ts)
        active = _active_integrations(self.config)

        # Get Notion context for enrichment
        notion_context = ""
        if self.notion:
            try:
                notion_context = self.notion.get_context_for_request(text)
            except Exception:
                logger.warning("Notion context enrichment failed", exc_info=True)

        # Build intern backstory from JD
        responsibilities = "\n".join(f"- {r}" for r in intern.responsibilities)
        tools_desc = self.tool_registry.get_tool_descriptions_for_prompt(intern)
        backstory = (
            f"You are {intern.name}, a {intern.role}.\n\n"
            f"Responsibilities:\n{responsibilities}\n\n"
            f"Communication style: {intern.tone}\n\n"
            f"Autonomy rules: {intern.autonomy_rules}\n\n"
            f"Tools available:\n{tools_desc}\n\n"
            f"IMPORTANT: For write operations (creating/updating tasks, projects, notes, etc.), "
            f"respond with ONLY a JSON action plan. For questions and read-only requests, answer directly."
        )

        # Get CrewAI tools for this intern
        crewai_tools = self.tool_registry.get_crewai_tools_for_intern(intern)

        # Memory context
        memory_context = intern.get_memory_context()

        response = self.runner.run_for_intern(
            user_message=text,
            intern_name=intern.name,
            intern_role=intern.role,
            intern_backstory=backstory,
            tools=crewai_tools,
            notion_context=notion_context,
            history=history,
            memory_context=memory_context,
            active_integrations=active,
        )

        self._add_to_history(thread_ts, "user", text)
        self._thread_intern[thread_ts] = intern.name.lower()

        prefix = f"*[{intern.name} — {intern.role}]*\n"

        if isinstance(response, dict) and response.get("type") == "action_plan":
            # Check tool permissions before proposing
            for step in response.get("steps", []):
                service = step.get("service", "")
                action = step.get("action", "")
                if not self.tool_registry.can_execute(intern, service, action):
                    self._post(
                        channel, thread_ts,
                        f"{prefix}⚠️ I don't have permission to use `{service}/{action}`. "
                        f"My allowed tools: {', '.join(intern.tools_allowed)}",
                    )
                    return

            plan_text = self._format_plan(response)
            self._post(channel, thread_ts, f"{prefix}{plan_text}")
            self.approval.set_pending(thread_ts, response, channel)
            self._add_to_history(thread_ts, "assistant", plan_text)
        else:
            response_str = str(response)
            self._post(channel, thread_ts, f"{prefix}{response_str}")
            self._add_to_history(thread_ts, "assistant", response_str)

            # Update intern memory with a summary of the interaction
            if len(response_str) > 20:
                intern.add_memory(f"User asked: {text[:100]}... → Responded about {intern.role} duties")

    # ------------------------------------------------------------------
    # Jibsa orchestrator handlers
    # ------------------------------------------------------------------

    def _handle_approval_response(
        self,
        ctx,
        channel: str,
        thread_ts: str,
        text: str,
    ) -> None:
        if self.approval.is_approval(text):
            logger.info("Thread %s — plan approved", thread_ts)
            intern_name = self._thread_intern.get(thread_ts)
            self._execute_plan(ctx.pending_plan, channel, thread_ts, intern_name)
            self.approval.clear(thread_ts)

        elif self.approval.is_rejection(text):
            logger.info("Thread %s — plan rejected", thread_ts)
            self._post(channel, thread_ts, "Understood. What would you like to change?")
            self.approval.clear(thread_ts)

        else:
            self._post(
                channel,
                thread_ts,
                "I still have a pending plan waiting for your decision. "
                "Reply ✅ to proceed or ❌ to cancel.",
            )

    def _handle_new_request(
        self,
        channel: str,
        thread_ts: str,
        user: str,
        text: str,
    ) -> None:
        """Handle a message directed to Jibsa (orchestrator) via CrewAI."""
        history = self._get_history(thread_ts)
        active = _active_integrations(self.config)

        notion_context = ""
        if self.notion:
            try:
                notion_context = self.notion.get_context_for_request(text)
            except Exception:
                logger.warning("Notion context enrichment failed", exc_info=True)

        crewai_tools = self.tool_registry.get_crewai_tools_for_jibsa()

        response = self.runner.run_for_jibsa(
            user_message=text,
            tools=crewai_tools,
            notion_context=notion_context,
            history=history,
            active_integrations=active,
        )

        self._add_to_history(thread_ts, "user", text)
        self._thread_intern[thread_ts] = None

        if isinstance(response, dict) and response.get("type") == "action_plan":
            plan_text = self._format_plan(response)
            self._post(channel, thread_ts, plan_text)
            self.approval.set_pending(thread_ts, response, channel)
            self._add_to_history(thread_ts, "assistant", plan_text)
        else:
            self._post(channel, thread_ts, str(response))
            self._add_to_history(thread_ts, "assistant", str(response))

    def _execute_plan(
        self,
        plan: dict,
        channel: str,
        thread_ts: str,
        intern_name: str | None = None,
    ) -> None:
        steps = plan.get("steps", [])
        logger.info("Executing plan '%s' (%d steps)", plan.get("summary", ""), len(steps))

        intern = self.intern_registry.get_intern(intern_name) if intern_name else None

        results = []
        for step in steps:
            service = step.get("service", "")

            if intern and not self.tool_registry.can_execute(intern, service, step.get("action", "")):
                result = {"ok": False, "error": f"'{intern.name}' lacks permission for {service}/{step.get('action')}"}
            elif service == "notion" and self.notion:
                result = self.notion.execute_step(step)
            else:
                result = {"ok": False, "error": f"'{service}' not connected yet"}
            results.append((step, result))
            logger.info("Step %s/%s → ok=%s", step.get("action"), service, result.get("ok"))

        lines = [f"✅ *{plan.get('summary', 'Plan')} — complete*\n"]
        for step, result in results:
            desc = step.get("description") or step.get("action", "step")
            if result.get("ok"):
                url = result.get("url", "")
                suffix = f" → <{url}|view>" if url else ""
                lines.append(f"  ✅ {desc}{suffix}")
            else:
                lines.append(f"  ⚠️ {desc} — {result.get('error', 'unknown error')}")

        prefix = ""
        if intern:
            prefix = f"*[{intern.name} — {intern.role}]*\n"

        self._post(channel, thread_ts, f"{prefix}{chr(10).join(lines)}")

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def _format_plan(self, plan: dict) -> str:
        lines = [f"📋 *Plan: {plan.get('summary', 'Proposed Action')}*\n"]
        for i, step in enumerate(plan.get("steps", []), 1):
            desc = step.get("description") or f"{step.get('action')} on {step.get('service')}"
            lines.append(f"  {i}. {desc}")
        lines.append("\nApprove? ✅ / ❌")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def _get_history(self, thread_ts: str) -> list[dict]:
        return list(self._history.get(thread_ts, []))

    def _add_to_history(self, thread_ts: str, role: str, content: str) -> None:
        if thread_ts not in self._history:
            self._history[thread_ts] = []
        self._history[thread_ts].append({"role": role, "content": content})
        if len(self._history[thread_ts]) > self._max_history:
            self._history[thread_ts] = self._history[thread_ts][-self._max_history:]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _post(self, channel: str, thread_ts: str, text: str) -> None:
        try:
            self.slack.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=text,
            )
        except Exception as e:
            logger.error("Failed to post to Slack: %s", e)
