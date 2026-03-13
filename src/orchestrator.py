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
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .approval import ApprovalManager, ApprovalState
from .circuit_breaker import CircuitBreaker, CircuitOpenError
from .crew_runner import CrewRunner
from .hire_flow import HireFlowManager
from .metrics import MetricsTracker
from .intern_registry import InternRegistry
from .integrations.notion_second_brain import build_second_brain
from .models.intern import InternJD
from .router import MessageRouter, RouteResult
from .tool_registry import ToolRegistry
from .tools.notion_read_tool import NotionReadTool
from .tools.web_search_tool import WebSearchTool
from .tools.code_exec_tool import CodeExecTool
from .tools.slack_tool import SlackTool
from .tools.calendar_tool import CalendarTool
from .tools.file_gen_tool import FileGenTool
from .tools.image_gen_tool import ImageGenTool
from .tools.reminder_tool import ReminderTool
from .tools.web_reader_tool import WebReaderTool
from .scheduler import ReminderScheduler

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"

# Commands handled directly by the orchestrator (not routed to interns/LLM)
MANAGEMENT_COMMANDS = {
    "list interns", "team", "interns", "show team", "stats", "reminders",
}


def _active_integrations(config: dict) -> list[str]:
    """Return list of enabled integration names from config."""
    return [
        name
        for name, cfg in config.get("integrations", {}).items()
        if cfg.get("enabled", False)
    ]


def _parse_reminder_time(when: str, timezone: str = "UTC"):
    """Parse a time string into a datetime. Supports ISO 8601 and relative times."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    import re

    tz = ZoneInfo(timezone)
    now = datetime.now(tz)

    # Try ISO 8601 first
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(when.strip(), fmt)
            return dt.replace(tzinfo=tz)
        except ValueError:
            continue

    # Try relative: "in X minutes/hours/days"
    match = re.match(r"in\s+(\d+)\s+(minute|min|hour|hr|day)s?", when.strip(), re.IGNORECASE)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        if unit in ("minute", "min"):
            return now + timedelta(minutes=amount)
        elif unit in ("hour", "hr"):
            return now + timedelta(hours=amount)
        elif unit == "day":
            return now + timedelta(days=amount)

    # Try "tomorrow at HH:MM"
    match = re.match(r"tomorrow\s+(?:at\s+)?(\d{1,2}):?(\d{2})?\s*(am|pm)?", when.strip(), re.IGNORECASE)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)
        if ampm and ampm.lower() == "pm" and hour < 12:
            hour += 12
        elif ampm and ampm.lower() == "am" and hour == 12:
            hour = 0
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)

    return None


class Orchestrator:
    def __init__(self, slack_client: Any, config: dict):
        self.slack = slack_client
        self.config = config
        self.notion = build_second_brain(config)
        self.approval = ApprovalManager(config)

        # Reminder scheduler
        tz = config.get("jibsa", {}).get("timezone", "UTC")
        self.reminder_scheduler = ReminderScheduler(slack_client, timezone=tz)
        self.reminder_scheduler.start()

        # CrewAI runner
        self.runner = CrewRunner(config)

        # Metrics
        self.metrics = MetricsTracker()

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

        # Circuit breaker for Notion API calls
        self._notion_circuit = CircuitBreaker("notion", failure_threshold=3, recovery_timeout=60)

    def _register_crewai_tools(self) -> None:
        """Create and register CrewAI tool instances."""
        # Notion read tool (available if Notion is connected)
        if self.notion:
            self.tool_registry.register_crewai_tool(
                "notion", NotionReadTool.create(self.notion)
            )

        # Web search tool (always available)
        self.tool_registry.register_crewai_tool("web_search", WebSearchTool())

        # Code execution tool (always available, config-driven limits)
        self.tool_registry.register_crewai_tool("code_exec", CodeExecTool.create(self.config))

        # Slack tool (write tool — proposes action plans)
        self.tool_registry.register_crewai_tool("slack", SlackTool())

        # Calendar tool (read-only stub for Phase 3)
        self.tool_registry.register_crewai_tool("calendar", CalendarTool())

        # File generation tool (write tool — proposes action plans)
        self.tool_registry.register_crewai_tool("file_gen", FileGenTool())

        # Image generation tool (write tool — proposes action plans)
        self.tool_registry.register_crewai_tool("image_gen", ImageGenTool())

        # Reminder tool (write tool — proposes action plans)
        self.tool_registry.register_crewai_tool("reminder", ReminderTool())

        # Web reader tool (read-only — fetches full page content via ZenRows)
        self.tool_registry.register_crewai_tool("web_reader", WebReaderTool())

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
        request_id = uuid.uuid4().hex[:8]
        start_time = time.monotonic()
        logger.info("[%s] message from %s in %s: %.80s", request_id, user, thread_ts, text)

        try:
            self._dispatch(channel, thread_ts, user, text)
        finally:
            elapsed = time.monotonic() - start_time
            logger.info("[%s] completed in %.1fs", request_id, elapsed)

    def _dispatch(
        self,
        channel: str,
        thread_ts: str,
        user: str,
        text: str,
    ) -> None:
        """Internal dispatch — separated for tracing wrapper."""
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
        cmd = route.message.lower().strip()
        if cmd in MANAGEMENT_COMMANDS:
            if cmd == "stats":
                self._handle_stats(channel, thread_ts)
            elif cmd == "reminders":
                self._handle_list_reminders(channel, thread_ts)
            else:
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

        if route.is_team:
            self._handle_team_request(channel, thread_ts, user, route.message, route.team_names)
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

    def _handle_stats(self, channel: str, thread_ts: str) -> None:
        """Display usage statistics."""
        self._post(channel, thread_ts, self.metrics.format_stats())

    def _handle_list_reminders(self, channel: str, thread_ts: str) -> None:
        """List pending reminders."""
        reminders = self.reminder_scheduler.list_reminders()
        if not reminders:
            self._post(channel, thread_ts, "No pending reminders.")
            return
        lines = ["*Pending Reminders:*\n"]
        for r in reminders:
            lines.append(f"  \u23f0 {r['message'][:60]} \u2014 {r['run_at']}")
        self._post(channel, thread_ts, "\n".join(lines))

    # ------------------------------------------------------------------
    # Hire flow
    # ------------------------------------------------------------------

    def _start_hire_flow(self, channel: str, thread_ts: str, user: str, text: str) -> None:
        """Start a conversational hire flow."""
        self.hire_flow.start_session(thread_ts, user, text)
        response = self.hire_flow.handle(thread_ts, user, text)
        self._post(channel, thread_ts, response)

    # ------------------------------------------------------------------
    # Team request handling
    # ------------------------------------------------------------------

    def _handle_team_request(
        self,
        channel: str,
        thread_ts: str,
        user: str,
        text: str,
        team_names: list[str],
    ) -> None:
        """Handle a multi-intern team request."""
        # Resolve intern objects
        team_interns = []
        for name in team_names:
            intern = self.intern_registry.get_intern(name)
            if not intern:
                self._post(channel, thread_ts, f"⚠️ Unknown intern: '{name}'")
                return
            team_interns.append(intern)

        history = self._get_history(thread_ts)
        active = _active_integrations(self.config)
        notion_context = self._get_notion_context(text)

        # Build team member specs
        team_specs = []
        for intern in team_interns:
            responsibilities = "\n".join(f"- {r}" for r in intern.responsibilities)
            tools_desc = self.tool_registry.get_tool_descriptions_for_prompt(intern)
            backstory = (
                f"You are {intern.name}, a {intern.role}.\n\n"
                f"Responsibilities:\n{responsibilities}\n\n"
                f"Communication style: {intern.tone}\n\n"
                f"Tools available:\n{tools_desc}"
            )
            crewai_tools = self.tool_registry.get_crewai_tools_for_intern(intern)
            team_specs.append({
                "name": intern.name,
                "role": intern.role,
                "backstory": backstory,
                "tools": crewai_tools,
            })

        names_str = " + ".join(i.name for i in team_interns)
        prefix = f"*[Team: {names_str}]*\n"

        response = self.runner.run_for_team(
            user_message=text,
            team=team_specs,
            notion_context=notion_context,
            history=history,
            active_integrations=active,
        )

        self._add_to_history(thread_ts, "user", text)

        if isinstance(response, dict) and response.get("type") == "action_plan":
            plan_text = self._format_plan(response)
            full_text = f"{prefix}{plan_text}"
            blocks = self._format_plan_blocks(response, full_text)
            blocks.insert(0, {
                "type": "section",
                "text": {"type": "mrkdwn", "text": prefix.strip()},
            })
            self._post_blocks(channel, thread_ts, blocks, full_text)
            self.approval.set_pending(thread_ts, response, channel)
            self._add_to_history(thread_ts, "assistant", plan_text)
        else:
            response_str = str(response)
            self._post(channel, thread_ts, f"{prefix}{response_str}")
            self._add_to_history(thread_ts, "assistant", response_str)

    # ------------------------------------------------------------------
    # Notion context (circuit-breaker protected)
    # ------------------------------------------------------------------

    def _get_notion_context(self, text: str) -> str:
        """Fetch Notion context with circuit breaker protection."""
        if not self.notion:
            return ""
        try:
            self._notion_circuit.check()
            context = self.notion.get_context_for_request(text)
            self._notion_circuit.record_success()
            return context
        except CircuitOpenError:
            logger.debug("Notion circuit open — skipping context enrichment")
            return ""
        except Exception:
            self._notion_circuit.record_failure()
            logger.warning("Notion context enrichment failed", exc_info=True)
            return ""

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
        notion_context = self._get_notion_context(text)

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
        memory_context = intern.get_memory_context(channel=channel)

        thinking_ts = self._post_thinking(channel, thread_ts)

        start = time.monotonic()
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
        latency = time.monotonic() - start

        is_plan = isinstance(response, dict) and response.get("type") == "action_plan"
        is_error = isinstance(response, str) and response.startswith("⚠️")
        self.metrics.record_request(intern.name.lower(), latency, was_action_plan=is_plan, error=is_error)

        if thinking_ts:
            self._delete_message(channel, thinking_ts)

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
            full_text = f"{prefix}{plan_text}"
            blocks = self._format_plan_blocks(response, full_text)
            # Prepend intern prefix block
            blocks.insert(0, {
                "type": "section",
                "text": {"type": "mrkdwn", "text": prefix.strip()},
            })
            self._post_blocks(channel, thread_ts, blocks, full_text)
            self.approval.set_pending(thread_ts, response, channel)
            self._add_to_history(thread_ts, "assistant", plan_text)
        else:
            response_str = str(response)
            self._post(channel, thread_ts, f"{prefix}{response_str}")
            self._add_to_history(thread_ts, "assistant", response_str)

            # Update intern memory with a summary of the interaction
            if len(response_str) > 20:
                intern.add_memory(f"User asked: {text[:100]}... → Responded about {intern.role} duties", channel=channel)

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
            self.metrics.record_approval(intern_name or "jibsa")
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

        notion_context = self._get_notion_context(text)

        crewai_tools = self.tool_registry.get_crewai_tools_for_jibsa()

        thinking_ts = self._post_thinking(channel, thread_ts)

        start = time.monotonic()
        response = self.runner.run_for_jibsa(
            user_message=text,
            tools=crewai_tools,
            notion_context=notion_context,
            history=history,
            active_integrations=active,
        )
        latency = time.monotonic() - start

        is_plan = isinstance(response, dict) and response.get("type") == "action_plan"
        is_error = isinstance(response, str) and response.startswith("⚠️")
        self.metrics.record_request("jibsa", latency, was_action_plan=is_plan, error=is_error)

        if thinking_ts:
            self._delete_message(channel, thinking_ts)

        self._add_to_history(thread_ts, "user", text)
        self._thread_intern[thread_ts] = None

        if isinstance(response, dict) and response.get("type") == "action_plan":
            plan_text = self._format_plan(response)
            blocks = self._format_plan_blocks(response, plan_text)
            self._post_blocks(channel, thread_ts, blocks, plan_text)
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
            elif service == "slack":
                result = self._execute_slack_step(step)
            elif service == "file_gen":
                result = self._execute_file_gen_step(step)
            elif service == "image_gen":
                result = self._execute_image_gen_step(step)
            elif service == "reminder":
                result = self._execute_reminder_step(step, channel, thread_ts)
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

        # Upload any generated files
        for step, result in results:
            if result.get("ok") and result.get("file_path"):
                try:
                    self.slack.files_upload_v2(
                        channel=channel,
                        thread_ts=thread_ts,
                        file=result["file_path"],
                        title=result.get("title", "Generated file"),
                    )
                except Exception as e:
                    logger.error("Failed to upload file: %s", e)
                    self._post(channel, thread_ts, f"⚠️ File upload failed: {e}")
                finally:
                    # Clean up temp file
                    Path(result["file_path"]).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Slack execution
    # ------------------------------------------------------------------

    def _execute_slack_step(self, step: dict) -> dict:
        """Execute a Slack write action (e.g. post_message)."""
        action = step.get("action", "")
        params = step.get("params", {})

        if action == "post_message":
            channel = params.get("channel", "")
            message = params.get("message", "")
            if not channel or not message:
                return {"ok": False, "error": "Missing channel or message"}
            try:
                self.slack.chat_postMessage(channel=channel, text=message)
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        return {"ok": False, "error": f"Unknown slack action: {action}"}

    # ------------------------------------------------------------------
    # File generation execution
    # ------------------------------------------------------------------

    def _execute_file_gen_step(self, step: dict) -> dict:
        """Execute a file generation step — create file and upload to Slack."""
        from .tools.file_gen_tool import create_and_get_path

        params = step.get("params", {})
        filename = params.get("filename", "")
        content = params.get("content", "")
        title = params.get("title", filename)

        if not filename or not content:
            return {"ok": False, "error": "Missing filename or content"}

        try:
            file_path = create_and_get_path(filename, content)
            # We'll store the path — the orchestrator will upload after all steps
            return {"ok": True, "file_path": file_path, "title": title}
        except Exception as e:
            return {"ok": False, "error": f"File creation failed: {e}"}

    # ------------------------------------------------------------------
    # Image generation execution
    # ------------------------------------------------------------------

    def _execute_image_gen_step(self, step: dict) -> dict:
        """Execute an image generation step — generate and save image."""
        from .tools.image_gen_tool import generate_and_save

        params = step.get("params", {})
        prompt = params.get("prompt", "")
        size = params.get("size", "1024x1024")
        filename = params.get("filename", "generated_image.png")

        if not prompt:
            return {"ok": False, "error": "Missing image prompt"}
        if not os.environ.get("OPENAI_API_KEY"):
            return {"ok": False, "error": "OPENAI_API_KEY is not set"}

        try:
            file_path = generate_and_save(prompt, size, filename)
            return {"ok": True, "file_path": file_path, "title": f"Generated: {prompt[:60]}"}
        except Exception as e:
            logger.error("Image generation failed: %s", e)
            return {"ok": False, "error": f"Image generation failed: {e}"}

    # ------------------------------------------------------------------
    # Reminder execution
    # ------------------------------------------------------------------

    def _execute_reminder_step(self, step: dict, channel: str, thread_ts: str) -> dict:
        """Schedule a reminder."""
        params = step.get("params", {})
        message = params.get("message", "")
        when = params.get("when", "")

        if not message or not when:
            return {"ok": False, "error": "Missing message or when"}

        run_at = _parse_reminder_time(when, self.config.get("jibsa", {}).get("timezone", "UTC"))
        if not run_at:
            return {"ok": False, "error": f"Could not parse time: '{when}'"}

        return self.reminder_scheduler.add_reminder(
            channel=channel,
            thread_ts=thread_ts,
            message=message,
            run_at=run_at,
        )

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

    def _format_plan_blocks(self, plan: dict, text_fallback: str) -> list[dict]:
        """Build Slack Block Kit blocks for an action plan with approve/reject buttons."""
        steps_text = ""
        for i, step in enumerate(plan.get("steps", []), 1):
            desc = step.get("description") or f"{step.get('action')} on {step.get('service')}"
            steps_text += f"{i}. {desc}\n"

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"📋 *Plan: {plan.get('summary', 'Proposed Action')}*",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": steps_text.strip(),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve"},
                        "style": "primary",
                        "action_id": "approve_plan",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Reject"},
                        "style": "danger",
                        "action_id": "reject_plan",
                    },
                ],
            },
        ]
        return blocks

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

    def _post_thinking(self, channel: str, thread_ts: str) -> str | None:
        """Post a 'thinking...' indicator and return its message ts for later deletion."""
        try:
            resp = self.slack.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="\U0001f4ad Thinking...",
            )
            return resp.get("ts")
        except Exception:
            logger.debug("Could not post thinking indicator")
            return None

    def _delete_message(self, channel: str, ts: str) -> None:
        """Delete a message (used to remove thinking indicator)."""
        try:
            self.slack.chat_delete(channel=channel, ts=ts)
        except Exception:
            logger.debug("Could not delete thinking indicator")

    def _post(self, channel: str, thread_ts: str, text: str) -> None:
        try:
            self.slack.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=text,
            )
        except Exception as e:
            logger.error("Failed to post to Slack: %s", e)

    def _post_blocks(self, channel: str, thread_ts: str, blocks: list[dict], text: str) -> None:
        """Post a Block Kit message with a text fallback."""
        try:
            self.slack.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                blocks=blocks,
                text=text,
            )
        except Exception as e:
            logger.error("Failed to post blocks to Slack: %s", e)
            # Fallback to plain text
            self._post(channel, thread_ts, text)

    # ------------------------------------------------------------------
    # Block Kit button handler (called from app.py)
    # ------------------------------------------------------------------

    def handle_button_action(self, action_id: str, channel: str, thread_ts: str, user: str, respond) -> None:
        """Handle Block Kit button clicks for approve/reject."""
        ctx = self.approval.get(thread_ts)
        if ctx.state != ApprovalState.PENDING:
            respond("No pending plan for this thread.")
            return

        if action_id == "approve_plan":
            logger.info("Thread %s — plan approved via button by %s", thread_ts, user)
            respond("✅ Approved! Executing...")
            intern_name = self._thread_intern.get(thread_ts)
            self._execute_plan(ctx.pending_plan, channel, thread_ts, intern_name)
            self.approval.clear(thread_ts)

        elif action_id == "reject_plan":
            logger.info("Thread %s — plan rejected via button by %s", thread_ts, user)
            respond("❌ Rejected. What would you like to change?")
            self.approval.clear(thread_ts)
