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
from .tools.jira_read_tool import JiraReadTool
from .tools.confluence_read_tool import ConfluenceReadTool
from .scheduler import ReminderScheduler
from .integrations.credential_store import CredentialStore
from .integrations.google_oauth import GoogleOAuthManager
from .context import current_user_id

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"

# Commands handled directly by the orchestrator (not routed to interns/LLM)
MANAGEMENT_COMMANDS = {
    "list interns", "team", "interns", "show team", "stats", "reminders",
    "history", "help", "my connections", "connections",
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


_PROVIDER_KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

# Integrations that have no implementation yet
_UNIMPLEMENTED_INTEGRATIONS = {"google_calendar", "gmail"}


def _validate_startup(config: dict) -> None:
    """Validate required env vars and config at startup. Raises on critical issues."""
    # 1. Slack tokens
    if not os.environ.get("SLACK_BOT_TOKEN"):
        raise EnvironmentError(
            "SLACK_BOT_TOKEN is not set. Add it to your .env file."
        )

    # 2. LLM provider API key
    provider = config.get("llm", {}).get("provider", "anthropic")
    env_key = _PROVIDER_KEY_MAP.get(provider)
    if env_key and not os.environ.get(env_key):
        raise EnvironmentError(
            f"LLM provider is '{provider}' but {env_key} is not set. "
            f"Add it to your .env file or change llm.provider in settings.yaml."
        )

    # 3. Notion token (if Notion integration is enabled)
    if config.get("integrations", {}).get("notion", {}).get("enabled", False):
        if not os.environ.get("NOTION_TOKEN"):
            logger.warning(
                "Notion integration is enabled but NOTION_TOKEN is not set — "
                "Notion features will be unavailable."
            )

    # 4. Warn about unimplemented integrations
    for name in _UNIMPLEMENTED_INTEGRATIONS:
        if config.get("integrations", {}).get(name, {}).get("enabled", False):
            logger.warning(
                "Integration '%s' is enabled in settings.yaml but not yet implemented "
                "(Phase 3+). It will be ignored.",
                name,
            )

    # 5. Warn about enabled scheduler jobs with no implementation
    for job_name, job_cfg in config.get("scheduler", {}).items():
        if isinstance(job_cfg, dict) and job_cfg.get("enabled", False):
            if job_name != "weekly_digest":  # weekly_digest is implemented
                logger.warning(
                    "Scheduler job '%s' is enabled but not yet implemented. "
                    "It will be ignored.",
                    job_name,
                )


class Orchestrator:
    def __init__(self, slack_client: Any, config: dict):
        _validate_startup(config)

        self.slack = slack_client
        self.config = config

        try:
            self.notion = build_second_brain(config)
        except Exception:
            logger.warning("Failed to initialize Notion Second Brain — continuing without it", exc_info=True)
            self.notion = None

        self.approval = ApprovalManager(config)

        # Jira client
        self.jira = self._build_jira_client(config)
        # Confluence client
        self.confluence_client = self._build_confluence_client(config)

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
        self._history_ts: dict[str, float] = {}  # thread_ts → last access time
        self._max_history: int = config.get("jibsa", {}).get("max_history", 20)
        self._max_threads: int = 500  # max threads to keep in memory

        # Track which intern is active per thread (for approval context)
        self._thread_intern: dict[str, str | None] = {}

        # Circuit breakers for external API calls
        self._notion_circuit = CircuitBreaker("notion", failure_threshold=3, recovery_timeout=60)
        self._jira_circuit = CircuitBreaker("jira", failure_threshold=3, recovery_timeout=60)
        self._confluence_circuit = CircuitBreaker("confluence", failure_threshold=3, recovery_timeout=60)

        # Approval history (completed/rejected plans)
        self._approval_history: list[dict] = []

        # Edit JD sessions: thread_ts → (intern_name, created_time)
        self._edit_sessions: dict[str, tuple[str, float]] = {}
        self._edit_session_ttl: float = 3600.0  # 1 hour TTL

        # Per-user credential store and Google OAuth
        db_path = config.get("jibsa", {}).get("credential_db_path", "data/credentials.db")
        self.credential_store = CredentialStore(db_path=db_path)
        self.google_oauth = GoogleOAuthManager(self.credential_store)

        # Pending OAuth flows: user_id → service name
        self._pending_oauth: dict[str, str] = {}

        # Register activity summary scheduler
        self._register_activity_summary()

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

        # Jira read tool (available if Jira is connected)
        if self.jira:
            self.tool_registry.register_crewai_tool(
                "jira", JiraReadTool.create(self.jira)
            )

        # Confluence read tool (available if Confluence is connected)
        if self.confluence_client:
            self.tool_registry.register_crewai_tool(
                "confluence", ConfluenceReadTool.create(self.confluence_client)
            )

    @staticmethod
    def _build_jira_client(config: dict):
        """Build JiraClient if Jira integration is enabled and env vars are set."""
        if not config.get("integrations", {}).get("jira", {}).get("enabled", False):
            return None
        server = os.environ.get("JIRA_SERVER", "")
        email = os.environ.get("JIRA_EMAIL", "")
        token = os.environ.get("JIRA_API_TOKEN", "")
        if not all([server, email, token]):
            logger.warning(
                "Jira integration is enabled but JIRA_SERVER/JIRA_EMAIL/JIRA_API_TOKEN "
                "are not all set — Jira features will be unavailable."
            )
            return None
        try:
            from .integrations.jira_client import JiraClient
            client = JiraClient(server, email, token)
            logger.info("Jira client initialized (%s)", server)
            return client
        except Exception:
            logger.warning("Failed to initialize Jira client", exc_info=True)
            return None

    @staticmethod
    def _build_confluence_client(config: dict):
        """Build ConfluenceClient if Confluence integration is enabled and env vars are set."""
        if not config.get("integrations", {}).get("confluence", {}).get("enabled", False):
            return None
        # Confluence shares Atlassian credentials with Jira
        server = os.environ.get("JIRA_SERVER", "")
        email = os.environ.get("JIRA_EMAIL", "")
        token = os.environ.get("JIRA_API_TOKEN", "")
        if not all([server, email, token]):
            logger.warning(
                "Confluence integration is enabled but JIRA_SERVER/JIRA_EMAIL/JIRA_API_TOKEN "
                "are not all set — Confluence features will be unavailable."
            )
            return None
        try:
            from .integrations.confluence_client import ConfluenceClient
            client = ConfluenceClient(server, email, token)
            logger.info("Confluence client initialized (%s)", server)
            return client
        except Exception:
            logger.warning("Failed to initialize Confluence client", exc_info=True)
            return None

    def _register_activity_summary(self) -> None:
        """Register a weekly activity summary job if enabled in config."""
        sched_cfg = self.config.get("scheduler", {}).get("weekly_digest", {})
        if not sched_cfg.get("enabled", False):
            return

        from apscheduler.triggers.cron import CronTrigger
        cron_str = sched_cfg.get("cron", "0 16 * * 5")  # default: Friday 4pm
        parts = cron_str.split()
        if len(parts) >= 5:
            tz = self.config.get("jibsa", {}).get("timezone", "UTC")
            trigger = CronTrigger(
                minute=parts[0], hour=parts[1], day=parts[2],
                month=parts[3], day_of_week=parts[4], timezone=tz,
            )
            self.reminder_scheduler._scheduler.add_job(
                self._post_activity_summary,
                trigger=trigger,
                id="jibsa_weekly_digest",
                replace_existing=True,
            )
            logger.info("Weekly activity summary scheduled: %s", cron_str)

    def _post_activity_summary(self) -> None:
        """Post an activity summary to the Jibsa channel."""
        channel = self.config.get("jibsa", {}).get("channel_name", "jibsa")

        stats_text = self.metrics.format_stats()
        history_summary = self._format_history_summary(limit=10)

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Weekly Activity Summary"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": stats_text},
            },
        ]

        if history_summary:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Recent Actions*\n{history_summary}"},
            })

        text = f"Weekly Activity Summary\n{stats_text}"
        self._post_blocks(channel, None, blocks, text)

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

        token = current_user_id.set(user)
        try:
            self._dispatch(channel, thread_ts, user, text)
        finally:
            current_user_id.reset(token)
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
        # Periodic cleanup of expired edit sessions
        self._cleanup_edit_sessions()

        # Priority 0.3: Pending OAuth code (user pasting auth code in DM)
        if user in self._pending_oauth and channel.startswith("D"):
            self._handle_oauth_code(channel, thread_ts, user, text)
            return

        # Priority 0.5: Active edit session
        if thread_ts in self._edit_sessions:
            self._handle_edit_response(channel, thread_ts, user, text)
            return

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
            elif cmd == "history":
                self._handle_history(channel, thread_ts)
            elif cmd == "help":
                self._handle_help(channel, thread_ts)
            elif cmd in ("my connections", "connections"):
                self._handle_list_connections(channel, thread_ts, user)
            else:
                self._handle_list_interns(channel, thread_ts)
            return

        # Connect/disconnect service
        if cmd.startswith("connect ") and not route.intern_name:
            service = cmd[8:].strip()
            self._handle_connect(channel, thread_ts, user, service)
            return
        if cmd.startswith("disconnect ") and not route.intern_name:
            service = cmd[11:].strip()
            self._handle_disconnect(channel, thread_ts, user, service)
            return

        # Help with target: "help alex"
        if route.message.lower().startswith("help ") and not route.intern_name:
            target = route.message[5:].strip()
            self._handle_help(channel, thread_ts, target=target)
            return

        # Edit JD: "edit alex's jd" or "edit alex"
        if route.message.lower().startswith("edit ") and not route.intern_name:
            intern_name = route.message[5:].strip().rstrip("'s jd").rstrip("'s").strip()
            self._handle_edit_jd(channel, thread_ts, user, intern_name)
            return

        # Show intern JD: "show alex's jd" or "show alex"
        if route.message.lower().startswith("show ") and not route.intern_name:
            intern_name = route.message[5:].strip().rstrip("'s jd").rstrip("'s").strip()
            intern = self.intern_registry.get_intern(intern_name)
            if intern:
                self._show_jd_blocks(channel, thread_ts, intern)
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
        """List all active interns with Block Kit cards."""
        interns = self.intern_registry.list_interns(force_refresh=True)
        if not interns:
            self._post(channel, thread_ts,
                       "No interns yet. Say `hire a <role> intern` to create one.")
            return

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Your AI Interns"},
            },
        ]

        for i in interns:
            tools = ", ".join(f"`{t}`" for t in i.tools_allowed) or "_none_"
            resp_preview = i.responsibilities[0] if i.responsibilities else "No responsibilities set"
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{i.name}* — {i.role}\n{resp_preview}{'...' if len(i.responsibilities) > 1 else ''}",
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View JD"},
                    "action_id": f"view_jd_{i.name.lower()}",
                },
            })
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Tools: {tools} · Say `{i.name.lower()} <task>` to assign work"},
                ],
            })

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_{len(interns)} active intern{'s' if len(interns) != 1 else ''}_ · `hire a <role> intern` to add more"},
            ],
        })

        fallback = "\n".join(f"  *{i.name}* — {i.role}" for i in interns)
        self._post_blocks(channel, thread_ts, blocks, f"Your AI Interns:\n{fallback}")

    def _handle_fire_intern(self, channel: str, thread_ts: str, name: str) -> None:
        """Deactivate an intern."""
        result = self.intern_registry.deactivate_intern(name)
        if result.get("ok"):
            self.router.update_names(self.intern_registry.get_intern_names())
            self._post(channel, thread_ts, f"✅ Intern '{name}' has been deactivated.")
        else:
            self._post(channel, thread_ts, f"⚠️ {result.get('error', 'Could not fire intern')}")

    def _handle_stats(self, channel: str, thread_ts: str) -> None:
        """Display usage statistics with Block Kit."""
        stats_text = self.metrics.format_stats()
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Jibsa Stats"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": stats_text},
            },
        ]

        # Add recent history if available
        history_text = self._format_history_summary(limit=5)
        if history_text:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Recent Actions*\n{history_text}"},
            })

        self._post_blocks(channel, thread_ts, blocks, stats_text)

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
    # Help command
    # ------------------------------------------------------------------

    def _handle_help(self, channel: str, thread_ts: str, target: str = "") -> None:
        """Display contextual help. If target is an intern name, show intern-specific help."""
        if target:
            intern = self.intern_registry.get_intern(target)
            if intern:
                self._handle_help_intern(channel, thread_ts, intern)
                return

        interns = self.intern_registry.list_interns()
        intern_list = ", ".join(f"`{i.name.lower()}`" for i in interns) if interns else "_none yet_"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Jibsa Help"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Getting Started*\n"
                        "Jibsa is your AI intern manager. Create interns with specific roles, "
                        "assign them tasks, and they'll propose actions for your approval."
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Intern Management*\n"
                        "`hire a <role> intern` — Create a new intern\n"
                        "`list interns` — Show all active interns\n"
                        "`show <name>'s jd` — View an intern's Job Description\n"
                        "`edit <name>'s jd` — Edit an intern's Job Description\n"
                        "`fire <name>` — Deactivate an intern"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Assigning Tasks*\n"
                        "`<name> <request>` — Ask a specific intern\n"
                        "`ask <name> to <request>` — Ask a specific intern\n"
                        "`form team <name1>, <name2> to <task>` — Multi-intern collaboration\n"
                        "Or just talk to me directly — I'll handle it as the orchestrator."
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*System Commands*\n"
                        "`help` — This help message\n"
                        "`help <name>` — Help for a specific intern\n"
                        "`stats` — Usage statistics\n"
                        "`history` — Recent approval history\n"
                        "`reminders` — Pending reminders"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Approvals*\n"
                        "When an intern proposes an action, approve with ✅ or reject with ❌.\n"
                        "You can also click the Approve/Reject buttons."
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Active interns: {intern_list} | Run `jibsa doctor` in CLI to check system health",
                    },
                ],
            },
        ]

        self._post_blocks(channel, thread_ts, blocks, "Jibsa Help — use `help` for commands")

    def _handle_help_intern(self, channel: str, thread_ts: str, intern: InternJD) -> None:
        """Show help specific to an intern."""
        responsibilities = "\n".join(f"  • {r}" for r in intern.responsibilities)
        tools = ", ".join(f"`{t}`" for t in intern.tools_allowed) or "_none_"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Help: {intern.name}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{intern.role}*\n\n{responsibilities}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tone:* {intern.tone or 'Default'}"},
                    {"type": "mrkdwn", "text": f"*Tools:* {tools}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Autonomy:* {intern.autonomy_rules or 'Always propose before acting'}",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*How to use {intern.name}:*\n"
                        f"`{intern.name.lower()} <your request>` — assign a task\n"
                        f"`ask {intern.name.lower()} to <request>` — same thing\n"
                        f"`show {intern.name.lower()}'s jd` — view full JD\n"
                        f"`edit {intern.name.lower()}'s jd` — modify JD"
                    ),
                },
            },
        ]

        self._post_blocks(channel, thread_ts, blocks, f"Help for {intern.name}")

    # ------------------------------------------------------------------
    # Edit JD flow
    # ------------------------------------------------------------------

    def _handle_edit_jd(self, channel: str, thread_ts: str, user: str, intern_name: str) -> None:
        """Start an edit JD session."""
        intern = self.intern_registry.get_intern(intern_name)
        if not intern:
            self._post(
                channel, thread_ts,
                f"No intern named '{intern_name}'. "
                f"Available: {', '.join(self.intern_registry.get_intern_names()) or 'none'}",
            )
            return

        self._edit_sessions[thread_ts] = (intern.name.lower(), time.time())

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Editing {intern.name}'s Job Description*",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": intern.format_jd()},
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "Tell me what you'd like to change. Examples:\n"
                        "• _change tone to casual and friendly_\n"
                        "• _add responsibility: write blog posts_\n"
                        "• _add tool: web_search_\n"
                        "• _remove responsibility: update social media_\n"
                        "• _change role to Senior Content Strategist_\n\n"
                        "Say `done` or `cancel` when finished."
                    ),
                },
            },
        ]
        self._post_blocks(channel, thread_ts, blocks, f"Editing {intern.name}'s JD — tell me what to change.")

    def _handle_edit_response(self, channel: str, thread_ts: str, user: str, text: str) -> None:
        """Process an edit instruction for an active edit session."""
        session = self._edit_sessions.get(thread_ts)
        intern_name = session[0] if session else ""
        text_lower = text.lower().strip()

        if text_lower in ("done", "cancel", "exit", "quit", "nevermind"):
            del self._edit_sessions[thread_ts]
            self._post(channel, thread_ts, "Edit session ended.")
            return

        intern = self.intern_registry.get_intern(intern_name)
        if not intern:
            del self._edit_sessions[thread_ts]
            self._post(channel, thread_ts, f"Intern '{intern_name}' no longer exists. Edit cancelled.")
            return

        # Parse edit instructions
        updates = self._parse_edit_instruction(text, intern)
        if not updates:
            # Use CrewAI to interpret natural language edits
            updates = self._parse_edit_with_llm(text, intern)

        if not updates:
            self._post(
                channel, thread_ts,
                "I couldn't understand that edit. Try being more specific, e.g.:\n"
                "• `change tone to casual`\n"
                "• `add responsibility: draft reports`\n"
                "• `add tool: web_search`\n"
                "• `remove tool: code_exec`",
            )
            return

        # Apply and show result
        result = self.intern_registry.update_intern(intern_name, updates)
        if result.get("ok"):
            # Refresh and show updated JD
            updated = self.intern_registry.get_intern(intern_name)
            if updated:
                self._post(
                    channel, thread_ts,
                    f"✅ Updated {updated.name}'s JD:\n\n{updated.format_jd()}\n\n"
                    f"_Tell me more changes, or say `done` to finish._",
                )
            else:
                self._post(channel, thread_ts, "✅ Updated. Say `done` to finish editing.")
        else:
            self._post(channel, thread_ts, f"⚠️ Update failed: {result.get('error', 'unknown')}")

    def _parse_edit_instruction(self, text: str, intern: InternJD) -> dict | None:
        """Parse simple edit patterns without LLM.

        Returns a dict of updates or None if the pattern isn't recognized.
        """
        import re
        text_stripped = text.strip()

        # "change tone to X" / "set tone to X"
        m = re.match(r"(?:change|set|update)\s+tone\s+to\s+(.+)", text_stripped, re.IGNORECASE)
        if m:
            return {"tone": m.group(1).strip()}

        # "change role to X" / "set role to X"
        m = re.match(r"(?:change|set|update)\s+role\s+to\s+(.+)", text_stripped, re.IGNORECASE)
        if m:
            return {"role": m.group(1).strip()}

        # "change autonomy rules to X"
        m = re.match(r"(?:change|set|update)\s+autonomy\s*(?:rules?)?\s+to\s+(.+)", text_stripped, re.IGNORECASE)
        if m:
            return {"autonomy_rules": m.group(1).strip()}

        # "add responsibility: X"
        m = re.match(r"add\s+responsibilit(?:y|ies)\s*[:]\s*(.+)", text_stripped, re.IGNORECASE)
        if m:
            new_resp = intern.responsibilities + [m.group(1).strip()]
            return {"responsibilities": new_resp}

        # "remove responsibility: X"
        m = re.match(r"remove\s+responsibilit(?:y|ies)\s*[:]\s*(.+)", text_stripped, re.IGNORECASE)
        if m:
            target = m.group(1).strip().lower()
            new_resp = [r for r in intern.responsibilities if target not in r.lower()]
            if len(new_resp) == len(intern.responsibilities):
                return None  # nothing matched
            return {"responsibilities": new_resp}

        # "add tool: X"
        m = re.match(r"add\s+tool\s*[:]\s*(\w+)", text_stripped, re.IGNORECASE)
        if m:
            tool = m.group(1).strip().lower()
            from .models.intern import VALID_TOOL_NAMES
            if tool not in VALID_TOOL_NAMES:
                return None
            if tool not in intern.tools_allowed:
                return {"tools_allowed": intern.tools_allowed + [tool]}
            return {}  # already has it

        # "remove tool: X"
        m = re.match(r"remove\s+tool\s*[:]\s*(\w+)", text_stripped, re.IGNORECASE)
        if m:
            tool = m.group(1).strip().lower()
            new_tools = [t for t in intern.tools_allowed if t.lower() != tool]
            return {"tools_allowed": new_tools}

        return None

    def _parse_edit_with_llm(self, text: str, intern: InternJD) -> dict | None:
        """Use CrewAI to interpret natural language edits."""
        import json

        prompt = (
            f"You are editing an intern's Job Description. Current JD:\n"
            f"- Role: {intern.role}\n"
            f"- Responsibilities: {', '.join(intern.responsibilities)}\n"
            f"- Tone: {intern.tone}\n"
            f"- Tools: {', '.join(intern.tools_allowed)}\n"
            f"- Autonomy rules: {intern.autonomy_rules}\n\n"
            f"The user wants to make this change: \"{text}\"\n\n"
            f"Return ONLY a JSON object with the fields to update. "
            f"Only include fields that should change. Supported fields:\n"
            f"- role (string)\n"
            f"- responsibilities (list of strings — full updated list)\n"
            f"- tone (string)\n"
            f"- tools_allowed (list of strings)\n"
            f"- autonomy_rules (string)\n\n"
            f"Example: {{\"tone\": \"Casual and friendly\", \"responsibilities\": [\"Write blog posts\", \"Draft emails\"]}}\n"
            f"If the instruction doesn't make sense, return {{}}"
        )

        try:
            response = self.runner.run_for_hire(
                user_message=prompt,
                available_tools="",
                history=None,
            )
            # Extract JSON from response
            response_text = str(response).strip()
            # Try to find JSON in the response
            import re
            json_match = re.search(r"\{[^{}]*\}", response_text, re.DOTALL)
            if json_match:
                updates = json.loads(json_match.group())
                # Filter to valid keys
                valid_keys = {"role", "responsibilities", "tone", "tools_allowed", "autonomy_rules"}
                updates = {k: v for k, v in updates.items() if k in valid_keys}
                return updates if updates else None
        except Exception as e:
            logger.warning("LLM edit parsing failed: %s", e)

        return None

    # ------------------------------------------------------------------
    # Approval history
    # ------------------------------------------------------------------

    def _record_history(self, plan: dict, intern_name: str | None, status: str) -> None:
        """Record a completed/rejected plan to history."""
        self._approval_history.append({
            "summary": plan.get("summary", "Unknown plan"),
            "intern": intern_name or "jibsa",
            "steps": len(plan.get("steps", [])),
            "status": status,
            "timestamp": time.time(),
        })
        # Keep last 50
        if len(self._approval_history) > 50:
            self._approval_history = self._approval_history[-50:]

    def _handle_history(self, channel: str, thread_ts: str) -> None:
        """Display recent approval history."""
        if not self._approval_history:
            self._post(channel, thread_ts, "No approval history yet.")
            return

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Approval History"},
            },
        ]

        from datetime import datetime
        for entry in reversed(self._approval_history[-10:]):
            ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%b %d %H:%M")
            status_icon = "✅" if entry["status"] == "approved" else "❌"
            intern_display = entry["intern"].capitalize() if entry["intern"] != "jibsa" else "Jibsa"

            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"{status_icon} *{entry['summary']}*\n{intern_display} · {entry['steps']} step(s) · {ts}",
                },
            })

        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"_Showing last {min(10, len(self._approval_history))} of {len(self._approval_history)} total_"},
            ],
        })

        self._post_blocks(channel, thread_ts, blocks, "Approval History")

    def _format_history_summary(self, limit: int = 10) -> str:
        """Format recent history as plain text (for activity summary)."""
        if not self._approval_history:
            return ""
        lines = []
        for entry in reversed(self._approval_history[-limit:]):
            from datetime import datetime
            ts = datetime.fromtimestamp(entry["timestamp"]).strftime("%b %d %H:%M")
            icon = "✅" if entry["status"] == "approved" else "❌"
            lines.append(f"{icon} {entry['summary']} ({entry['intern']}, {ts})")
        return "\n".join(lines)

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
            self._record_history(ctx.pending_plan, intern_name, "approved")
            self._execute_plan(ctx.pending_plan, channel, thread_ts, intern_name)
            self.approval.clear(thread_ts)

        elif self.approval.is_rejection(text):
            logger.info("Thread %s — plan rejected", thread_ts)
            intern_name = self._thread_intern.get(thread_ts)
            self._record_history(ctx.pending_plan, intern_name, "rejected")
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
                try:
                    self._notion_circuit.check()
                    result = self.notion.execute_step(step)
                    self._notion_circuit.record_success()
                except CircuitOpenError:
                    result = {"ok": False, "error": "Notion is temporarily unavailable (circuit open). Try again later."}
                except Exception as e:
                    self._notion_circuit.record_failure()
                    result = {"ok": False, "error": f"Notion error: {e}"}
            elif service == "slack":
                result = self._execute_slack_step(step)
            elif service == "file_gen":
                result = self._execute_file_gen_step(step)
            elif service == "image_gen":
                result = self._execute_image_gen_step(step)
            elif service == "reminder":
                result = self._execute_reminder_step(step, channel, thread_ts)
            elif service == "jira" and self.jira:
                try:
                    self._jira_circuit.check()
                    result = self.jira.execute_step(step)
                    self._jira_circuit.record_success()
                except CircuitOpenError:
                    result = {"ok": False, "error": "Jira is temporarily unavailable (circuit open). Try again later."}
                except Exception as e:
                    self._jira_circuit.record_failure()
                    result = {"ok": False, "error": f"Jira error: {e}"}
            elif service == "confluence" and self.confluence_client:
                try:
                    self._confluence_circuit.check()
                    result = self.confluence_client.execute_step(step)
                    self._confluence_circuit.record_success()
                except CircuitOpenError:
                    result = {"ok": False, "error": "Confluence is temporarily unavailable (circuit open). Try again later."}
                except Exception as e:
                    self._confluence_circuit.record_failure()
                    result = {"ok": False, "error": f"Confluence error: {e}"}
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
        aspect_ratio = params.get("aspect_ratio", "1:1")
        filename = params.get("filename", "generated_image.png")

        if not prompt:
            return {"ok": False, "error": "Missing image prompt"}
        if not os.environ.get("GOOGLE_API_KEY"):
            return {"ok": False, "error": "GOOGLE_API_KEY is not set"}

        try:
            file_path = generate_and_save(prompt, aspect_ratio, filename)
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
    # Rich Block Kit display
    # ------------------------------------------------------------------

    def _show_jd_blocks(self, channel: str, thread_ts: str, intern: InternJD) -> None:
        """Display an intern's JD using Block Kit."""
        responsibilities = "\n".join(f"  • {r}" for r in intern.responsibilities)
        tools = ", ".join(f"`{t}`" for t in intern.tools_allowed) or "_none_"

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{intern.name}'s Job Description"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Role:*\n{intern.role}"},
                    {"type": "mrkdwn", "text": f"*Status:*\n{'Active' if intern.active else 'Inactive'}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Responsibilities:*\n{responsibilities}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tone:*\n{intern.tone or 'Default'}"},
                    {"type": "mrkdwn", "text": f"*Tools:*\n{tools}"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Autonomy Rules:*\n{intern.autonomy_rules or 'Always propose before acting'}",
                },
            },
        ]

        # Memory stats
        mem_count = len(intern.memory)
        chan_mem = sum(len(v) for v in intern.channel_memory.values())
        if mem_count or chan_mem:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Memory: {mem_count} global, {chan_mem} channel-scoped entries"},
                ],
            })

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"`edit {intern.name.lower()}'s jd` to modify · `fire {intern.name.lower()}` to deactivate"},
            ],
        })

        self._post_blocks(channel, thread_ts, blocks, f"{intern.name}'s JD:\n{intern.format_jd()}")

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
        self._history_ts[thread_ts] = time.time()
        return list(self._history.get(thread_ts, []))

    def _add_to_history(self, thread_ts: str, role: str, content: str) -> None:
        if thread_ts not in self._history:
            self._history[thread_ts] = []
        self._history[thread_ts].append({"role": role, "content": content})
        self._history_ts[thread_ts] = time.time()
        if len(self._history[thread_ts]) > self._max_history:
            self._history[thread_ts] = self._history[thread_ts][-self._max_history:]
        # Evict oldest threads if over limit
        if len(self._history) > self._max_threads:
            self._evict_old_threads()

    def _evict_old_threads(self) -> None:
        """Remove oldest threads to keep memory bounded."""
        sorted_threads = sorted(self._history_ts.items(), key=lambda x: x[1])
        to_remove = len(self._history) - self._max_threads
        for ts, _ in sorted_threads[:to_remove]:
            self._history.pop(ts, None)
            self._history_ts.pop(ts, None)
            self._thread_intern.pop(ts, None)
        logger.debug("Evicted %d old threads from history", to_remove)

    def _cleanup_edit_sessions(self) -> None:
        """Remove expired edit sessions."""
        now = time.time()
        expired = [
            ts for ts, (_, created) in self._edit_sessions.items()
            if now - created > self._edit_session_ttl
        ]
        for ts in expired:
            self._edit_sessions.pop(ts, None)
            logger.debug("Expired edit session for thread %s", ts)

    # ------------------------------------------------------------------
    # Connection management (per-user OAuth)
    # ------------------------------------------------------------------

    _SUPPORTED_SERVICES = {"google"}

    def _handle_connect(self, channel: str, thread_ts: str, user: str, service: str) -> None:
        """Start an OAuth flow for a service."""
        if service not in self._SUPPORTED_SERVICES:
            self._post(channel, thread_ts, f"Unknown service '{service}'. Supported: {', '.join(sorted(self._SUPPORTED_SERVICES))}")
            return

        if service == "google":
            if not self.google_oauth.is_configured:
                self._post(channel, thread_ts, "Google OAuth is not configured. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in `.env`.")
                return

            # Check if already connected
            existing = self.credential_store.list_services(user)
            if "google" in existing:
                self._post(channel, thread_ts, "You're already connected to Google. Say `disconnect google` first if you want to reconnect.")
                return

            auth_url = self.google_oauth.generate_auth_url()
            if not auth_url:
                self._post(channel, thread_ts, "Failed to generate authorization URL.")
                return

            # DM the user with the auth URL
            try:
                dm = self.slack.conversations_open(users=user)
                dm_channel = dm["channel"]["id"]
                self.slack.chat_postMessage(
                    channel=dm_channel,
                    text=(
                        "*Connect Google Account*\n\n"
                        "1. Click the link below to authorize Jibsa:\n"
                        f"   {auth_url}\n\n"
                        "2. After authorizing, Google will show you a code.\n"
                        "3. *Paste that code here* (in this DM).\n\n"
                        "_This link expires in a few minutes._"
                    ),
                )
                self._pending_oauth[user] = service
                self._post(channel, thread_ts, "I've sent you a DM with the authorization link. Follow the steps there.")
            except Exception as e:
                logger.error("Failed to DM user %s: %s", user, e)
                self._post(channel, thread_ts, f"Couldn't send you a DM. Error: {e}")

    def _handle_disconnect(self, channel: str, thread_ts: str, user: str, service: str) -> None:
        """Revoke and delete stored credentials for a service."""
        if service not in self._SUPPORTED_SERVICES:
            self._post(channel, thread_ts, f"Unknown service '{service}'. Supported: {', '.join(sorted(self._SUPPORTED_SERVICES))}")
            return

        if service == "google":
            result = self.google_oauth.revoke_and_delete(user)
            if result["ok"]:
                self._post(channel, thread_ts, "Disconnected from Google. Your credentials have been revoked and deleted.")
            else:
                self._post(channel, thread_ts, f"Could not disconnect: {result['error']}")

    def _handle_list_connections(self, channel: str, thread_ts: str, user: str) -> None:
        """List the user's connected services."""
        services = self.credential_store.list_services(user)
        if not services:
            self._post(channel, thread_ts, "You have no connected services. Say `connect google` to get started.")
            return

        lines = ["*Your Connected Services*\n"]
        for svc in services:
            lines.append(f"  - {svc}")
        lines.append("\n_Say `disconnect <service>` to remove a connection._")
        self._post(channel, thread_ts, "\n".join(lines))

    def _handle_oauth_code(self, channel: str, thread_ts: str, user: str, text: str) -> None:
        """Handle a pasted OAuth authorization code in DM."""
        service = self._pending_oauth.pop(user, None)
        if not service:
            return

        code = text.strip()
        if not code or len(code) < 10:
            self._post(channel, thread_ts, "That doesn't look like a valid authorization code. Please try `connect google` again.")
            return

        if service == "google":
            result = self.google_oauth.exchange_code(user, code)
            if result["ok"]:
                self._post(channel, thread_ts, "Connected to Google! Your Calendar and Gmail are now available to your interns.")
            else:
                self._post(channel, thread_ts, f"Authorization failed: {result['error']}\n\nSay `connect google` to try again.")

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

    def _post_blocks(self, channel: str, thread_ts: str | None, blocks: list[dict], text: str) -> None:
        """Post a Block Kit message with a text fallback."""
        try:
            kwargs = {"channel": channel, "blocks": blocks, "text": text}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            self.slack.chat_postMessage(**kwargs)
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
            self.metrics.record_approval(intern_name or "jibsa")
            self._record_history(ctx.pending_plan, intern_name, "approved")
            self._execute_plan(ctx.pending_plan, channel, thread_ts, intern_name)
            self.approval.clear(thread_ts)

        elif action_id == "reject_plan":
            logger.info("Thread %s — plan rejected via button by %s", thread_ts, user)
            intern_name = self._thread_intern.get(thread_ts)
            self._record_history(ctx.pending_plan, intern_name, "rejected")
            respond("❌ Rejected. What would you like to change?")
            self.approval.clear(thread_ts)
