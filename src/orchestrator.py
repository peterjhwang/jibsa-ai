"""
Orchestrator — the central router.

Receives messages from Slack, manages conversation history per thread,
routes to the correct intern (or Jibsa itself), calls LLMRunner,
handles the propose-approve flow, and dispatches approved plans to
integration clients.

v0.5: Multi-intern support with routing, hire flow, and per-intern tool access.
"""
import logging
from pathlib import Path
from typing import Any

from .approval import ApprovalManager, ApprovalState
from .hire_flow import HireFlowManager
from .intern_registry import InternRegistry
from .llm_runner import LLMRunner
from .integrations.notion_second_brain import build_second_brain
from .models.intern import InternJD
from .router import MessageRouter, RouteResult
from .tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _active_integrations(config: dict) -> list[str]:
    """Return list of enabled integration names from config."""
    return [
        name
        for name, cfg in config.get("integrations", {}).items()
        if cfg.get("enabled", False)
    ]


def _load_text(path: Path) -> str:
    with open(path) as f:
        return f.read()


class Orchestrator:
    def __init__(self, slack_client: Any, config: dict):
        self.slack = slack_client
        self.config = config
        self.runner = LLMRunner(config)
        self.approval = ApprovalManager(config)
        self.notion = build_second_brain(config)
        self.tool_registry = ToolRegistry()
        self.intern_registry = InternRegistry(self.notion, config)
        self.router = MessageRouter(self.intern_registry.get_intern_names())

        # Load intern prompt template
        intern_prompt_path = _CONFIG_DIR / "prompts" / "intern.txt"
        self._intern_prompt_template = (
            _load_text(intern_prompt_path) if intern_prompt_path.exists() else None
        )

        # Hire flow uses its own system prompt
        hire_prompt_path = _CONFIG_DIR / "prompts" / "hire.txt"
        hire_runner = LLMRunner(
            config,
            system_prompt_template=_load_text(hire_prompt_path) if hire_prompt_path.exists() else None,
        )
        self.hire_flow = HireFlowManager(hire_runner, self.intern_registry, self.tool_registry)

        self._history: dict[str, list[dict]] = {}  # thread_ts → message list
        self._max_history: int = config.get("jibsa", {}).get("max_history", 20)

        # Track which intern is active per thread (for approval context)
        self._thread_intern: dict[str, str | None] = {}  # thread_ts → intern_name or None

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
            # Refresh router names after potential hire
            self.router.update_names(self.intern_registry.get_intern_names())
            return

        # Priority 2: Pending approval
        ctx = self.approval.get(thread_ts)
        if ctx.state == ApprovalState.PENDING:
            self._handle_approval_response(ctx, channel, thread_ts, text)
            return

        # Priority 3: Route the message
        route = self.router.route(text)

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
    # Hire flow
    # ------------------------------------------------------------------

    def _start_hire_flow(self, channel: str, thread_ts: str, user: str, text: str) -> None:
        """Start a conversational hire flow."""
        self.hire_flow.start_session(thread_ts, user, text)
        response = self.hire_flow.handle(thread_ts, user, text)
        self._post(channel, thread_ts, response)

    # ------------------------------------------------------------------
    # Intern request handling
    # ------------------------------------------------------------------

    def _handle_intern_request(
        self,
        channel: str,
        thread_ts: str,
        user: str,
        text: str,
        intern: InternJD,
    ) -> None:
        """Handle a request routed to a specific intern."""
        history = self._get_history(thread_ts)
        active = _active_integrations(self.config)

        notion_context = ""
        if self.notion:
            try:
                notion_context = self.notion.get_context_for_request(text)
            except Exception:
                logger.warning("Notion context enrichment failed", exc_info=True)

        # Build intern-specific system prompt replacements
        tools_desc = self.tool_registry.get_tool_descriptions_for_prompt(intern)
        responsibilities = "\n".join(f"- {r}" for r in intern.responsibilities)

        # Build valid actions list for the intern's allowed tools
        valid_actions_lines = []
        intern_tools = self.tool_registry.get_tools_for_intern(intern)
        for tool_name, tool_info in intern_tools.items():
            valid_actions_lines.append(f"Valid {tool_name} actions:")
            for action in tool_info["actions"]:
                valid_actions_lines.append(f"- {action}")
        valid_actions = "\n".join(valid_actions_lines)

        extra = {
            "{intern_name}": intern.name,
            "{intern_role}": intern.role,
            "{responsibilities}": responsibilities,
            "{intern_tone}": intern.tone,
            "{autonomy_rules}": intern.autonomy_rules,
            "{tools}": tools_desc,
            "{valid_actions}": valid_actions,
        }

        # Use intern-specific prompt template if available
        runner = self.runner
        if self._intern_prompt_template:
            runner = LLMRunner(self.config, system_prompt_template=self._intern_prompt_template)

        response = runner.run(
            user_message=text,
            history=history,
            active_integrations=active,
            notion_context=notion_context,
            extra_replacements=extra,
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
            self._post(channel, thread_ts, f"{prefix}{response}")
            self._add_to_history(thread_ts, "assistant", str(response))

    # ------------------------------------------------------------------
    # Jibsa orchestrator handlers (original behavior)
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
        history = self._get_history(thread_ts)
        active = _active_integrations(self.config)

        notion_context = ""
        if self.notion:
            try:
                notion_context = self.notion.get_context_for_request(text)
            except Exception:
                logger.warning("Notion context enrichment failed", exc_info=True)

        response = self.runner.run(
            user_message=text,
            history=history,
            active_integrations=active,
            notion_context=notion_context,
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

        # If plan came from an intern, check tool permissions
        intern = self.intern_registry.get_intern(intern_name) if intern_name else None

        results = []
        for step in steps:
            service = step.get("service", "")

            # Permission check for intern-initiated plans
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

        self._post(channel, thread_ts, f"{prefix}{''.join(lines) if prefix else chr(10).join(lines)}")

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
