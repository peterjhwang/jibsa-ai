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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .approval import ApprovalManager, ApprovalState
from .circuit_breaker import CircuitBreaker, CircuitOpenError
from .crew_runner import CrewRunner
from .hire_flow import HireFlowManager
from .sop_flow import SOPFlowManager
from .metrics import MetricsTracker
from .intern_registry import InternRegistry
from .integrations.audit_store import AuditStore
from .intern_templates import list_templates, get_template, template_to_jd
from .integrations.intern_store import InternStore
from .integrations.notion_second_brain import build_user_second_brain
from .models.intern import InternJD
from .router import MessageRouter, RouteResult
from .tool_registry import ToolRegistry
from .tools.notion_read_tool import NotionReadTool
from .tools.web_search_tool import WebSearchTool
from .tools.code_exec_tool import CodeExecTool
from .tools.slack_tool import SlackTool
from .tools.calendar_tool import CalendarReadTool
from .tools.gmail_tool import GmailReadTool
from .tools.drive_tool import DriveReadTool
from .tools.file_gen_tool import FileGenTool
from .tools.image_gen_tool import ImageGenTool
from .tools.reminder_tool import ReminderTool
from .tools.web_reader_tool import WebReaderTool
from .tools.jira_read_tool import JiraReadTool
from .tools.confluence_read_tool import ConfluenceReadTool
from .tools.delegate_tool import DelegateToInternTool
from .scheduler import ReminderScheduler
from .integrations.credential_store import CredentialStore
from .integrations.sop_store import SOPStore
from .integrations.schedule_store import ScheduleStore
from .integrations.google_oauth import GoogleOAuthManager
from .integrations.notion_oauth import NotionOAuthManager
from .integrations.notion_user_registry import NotionUserRegistry
from .sop_registry import SOPRegistry
from .context import current_user_id

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"

# Commands handled directly by the orchestrator (not routed to interns/LLM)
MANAGEMENT_COMMANDS = {
    "list interns", "team", "interns", "show team", "stats", "reminders",
    "history", "help", "audit", "templates", "my connections", "connections",
    "my schedules", "schedules",
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


# ---------------------------------------------------------------------------
# Schedule spec parser — "8:30am weekdays" → ("30 8 * * 1-5", "8:30 AM weekdays")
# ---------------------------------------------------------------------------

_DAY_MAP = {
    "monday": "0", "mon": "0",
    "tuesday": "1", "tue": "1",
    "wednesday": "2", "wed": "2",
    "thursday": "3", "thu": "3",
    "friday": "4", "fri": "4",
    "saturday": "5", "sat": "5",
    "sunday": "6", "sun": "6",
}


def _parse_time_part(text: str) -> tuple[int, int] | None:
    """Parse '8:30am', '5pm', '17:00' etc. → (hour, minute)."""
    import re
    m = re.match(r"(\d{1,2}):?(\d{2})?\s*(am|pm)?", text.strip(), re.IGNORECASE)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return (hour, minute)


def _parse_schedule_spec(text: str) -> tuple[str, str] | None:
    """Parse a natural-language schedule spec into (cron_string, description).

    Examples:
      "8:30am weekdays"       → ("30 8 * * 1-5", "8:30 AM weekdays")
      "5pm weekdays"          → ("0 17 * * 1-5", "5:00 PM weekdays")
      "friday 4pm"            → ("0 16 * * 5", "4:00 PM Fridays")
      "every day 9am"         → ("0 9 * * *", "9:00 AM daily")
      "every monday 9am"      → ("0 9 * * 1", "9:00 AM Mondays")
      "every weekday 8:30am"  → ("30 8 * * 1-5", "8:30 AM weekdays")
    """
    import re
    clean = text.strip().lower()
    clean = re.sub(r"^every\s+", "", clean)

    # Try to extract time from anywhere in the string
    time_match = re.search(r"(\d{1,2}):?(\d{2})?\s*(am|pm)?", clean, re.IGNORECASE)
    if not time_match:
        return None

    time_str = time_match.group(0)
    parsed = _parse_time_part(time_str)
    if not parsed:
        return None
    hour, minute = parsed

    # Remove the time part to get the day spec
    remainder = clean.replace(time_str, "").strip().strip(",").strip()

    # Determine day_of_week
    if remainder in ("weekdays", "weekday", "mon-fri"):
        dow = "1-5"
        desc_days = "weekdays"
    elif remainder in ("daily", "day", ""):
        dow = "*"
        desc_days = "daily"
    elif remainder in ("weekend", "weekends"):
        dow = "6,0"
        desc_days = "weekends"
    elif remainder in _DAY_MAP:
        dow = _DAY_MAP[remainder]
        desc_days = f"{remainder.capitalize()}s"
    else:
        # Try multi-day: "monday, wednesday, friday"
        parts = re.split(r"[,\s]+(?:and\s+)?", remainder)
        day_nums = []
        for p in parts:
            p = p.strip()
            if p in _DAY_MAP:
                day_nums.append(_DAY_MAP[p])
        if day_nums:
            dow = ",".join(day_nums)
            desc_days = ", ".join(p.strip().capitalize() for p in parts if p.strip() in _DAY_MAP)
        else:
            return None

    cron = f"{minute} {hour} * * {dow}"

    # Format description
    h12 = hour % 12 or 12
    ampm = "AM" if hour < 12 else "PM"
    min_str = f":{minute:02d}" if minute else ":00"
    description = f"{h12}{min_str} {ampm} {desc_days}"

    return (cron, description)


_PROVIDER_KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

# Integrations that have no implementation yet
_UNIMPLEMENTED_INTEGRATIONS: set[str] = set()


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

    # 5. Log enabled scheduler jobs
    for job_name, job_cfg in config.get("scheduler", {}).items():
        if isinstance(job_cfg, dict) and job_cfg.get("enabled", False):
            logger.info("Scheduler job '%s' is enabled", job_name)


class Orchestrator:
    def __init__(self, slack_client: Any, config: dict):
        _validate_startup(config)

        self.slack = slack_client
        self.config = config

        self.approval = ApprovalManager(config)

        # Jira client
        self.jira = self._build_jira_client(config)
        # Confluence client
        self.confluence_client = self._build_confluence_client(config)

        # Reminder scheduler
        tz = config.get("jibsa", {}).get("timezone", "UTC")
        scheduler_db = os.environ.get("JIBSA_DB_PATH") or config.get("jibsa", {}).get("intern_db_path", "data/jibsa.db")
        self.reminder_scheduler = ReminderScheduler(slack_client, timezone=tz, db_path=scheduler_db)
        self.reminder_scheduler.start()

        # CrewAI runner
        self.runner = CrewRunner(config)

        # Metrics
        self.metrics = MetricsTracker()

        # Audit log
        audit_db_path = os.environ.get("JIBSA_DB_PATH") or config.get("jibsa", {}).get("intern_db_path", "data/jibsa.db")
        self.audit = AuditStore(db_path=audit_db_path)

        # Per-user credential store and Google OAuth (before tool registration)
        cred_db_path = os.environ.get("CREDENTIAL_DB_PATH") or config.get("jibsa", {}).get("credential_db_path", "data/credentials.db")
        self.credential_store = CredentialStore(db_path=cred_db_path)
        self.google_oauth = GoogleOAuthManager(self.credential_store)
        self.notion_oauth = NotionOAuthManager(self.credential_store)
        self.notion_user_registry = NotionUserRegistry(self.credential_store)

        # Tool registry with CrewAI tool instances
        self.tool_registry = ToolRegistry()
        self._register_crewai_tools()

        # Intern management (SQLite-backed)
        intern_db_path = os.environ.get("JIBSA_DB_PATH") or config.get("jibsa", {}).get("intern_db_path", "data/jibsa.db")
        self.intern_store = InternStore(db_path=intern_db_path)
        self.intern_registry = InternRegistry(self.intern_store)
        self.router = MessageRouter(self.intern_registry.get_intern_names())

        # SOP management (SQLite-backed, same DB)
        self.sop_store = SOPStore(db_path=intern_db_path)
        self.sop_registry = SOPRegistry(self.sop_store)

        # User schedule store (SQLite-backed, same DB)
        self.schedule_store = ScheduleStore(db_path=intern_db_path)

        # Seed SOPs from YAML if present
        sop_yaml = _CONFIG_DIR / "sops.yaml"
        if sop_yaml.exists():
            SOPRegistry.seed_from_yaml(self.sop_store, str(sop_yaml))

        # Delegation tool (needs intern_registry + runner, so registered after them)
        self.tool_registry.register_crewai_tool(
            "delegate",
            DelegateToInternTool.create(
                intern_registry=self.intern_registry,
                crew_runner=self.runner,
                tool_registry=self.tool_registry,
                config=self.config,
                notion_oauth=self.notion_oauth,
                notion_user_registry=self.notion_user_registry,
            ),
        )

        # Hire flow
        self.hire_flow = HireFlowManager(self.runner, self.intern_registry, self.tool_registry)

        # SOP creation flow
        self.sop_flow = SOPFlowManager(self.runner, self.sop_registry, self.tool_registry)

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
        self._google_circuit = CircuitBreaker("google", failure_threshold=3, recovery_timeout=60)

        # Approval history (completed/rejected plans)
        self._approval_history: list[dict] = []

        # Edit JD sessions: thread_ts → (intern_name, created_time)
        self._edit_sessions: dict[str, tuple[str, float]] = {}
        self._edit_session_ttl: float = 3600.0  # 1 hour TTL

        # Pending OAuth flows: user_id → (service, channel, thread_ts)
        self._pending_oauth: dict[str, tuple[str, str, str]] = {}

        # Pending Notion post-connect setup: user_id → dm_channel
        self._pending_notion_setup: dict[str, str] = {}

        # Register scheduled jobs (config-based + user-defined)
        self._register_activity_summary()
        self._register_morning_briefing()
        self._register_eod_review()
        self._register_user_schedules()

    def _register_crewai_tools(self) -> None:
        """Create and register CrewAI tool instances."""
        # Notion read tool (per-user OAuth)
        self.tool_registry.register_crewai_tool(
            "notion", NotionReadTool.create(
                notion_oauth=self.notion_oauth,
                notion_user_registry=self.notion_user_registry,
                config=self.config,
            )
        )

        # Web search tool (always available)
        self.tool_registry.register_crewai_tool("web_search", WebSearchTool())

        # Code execution tool (always available, config-driven limits)
        self.tool_registry.register_crewai_tool("code_exec", CodeExecTool.create(self.config))

        # Slack tool (write tool — proposes action plans)
        self.tool_registry.register_crewai_tool("slack", SlackTool())

        # Google Calendar + Gmail (per-user OAuth — requires `connect google`)
        tz = self.config.get("jibsa", {}).get("timezone", "UTC")
        self.tool_registry.register_crewai_tool(
            "calendar", CalendarReadTool.create(self.google_oauth, tz)
        )
        self.tool_registry.register_crewai_tool(
            "gmail", GmailReadTool.create(self.google_oauth)
        )
        self.tool_registry.register_crewai_tool(
            "drive", DriveReadTool.create(self.google_oauth)
        )

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

        # Delegation tool registered after intern_registry init (see __init__)

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
    # Scheduled jobs: morning briefing + EOD review
    # ------------------------------------------------------------------

    def _register_cron_job(self, name: str, handler, config_key: str, default_cron: str) -> None:
        """Register a cron job from config if enabled."""
        sched_cfg = self.config.get("scheduler", {}).get(config_key, {})
        if not sched_cfg.get("enabled", False):
            return

        from apscheduler.triggers.cron import CronTrigger
        cron_str = sched_cfg.get("cron", default_cron)
        parts = cron_str.split()
        if len(parts) >= 5:
            tz = self.config.get("jibsa", {}).get("timezone", "UTC")
            trigger = CronTrigger(
                minute=parts[0], hour=parts[1], day=parts[2],
                month=parts[3], day_of_week=parts[4], timezone=tz,
            )
            self.reminder_scheduler._scheduler.add_job(
                handler, trigger=trigger, id=f"jibsa_{config_key}", replace_existing=True,
            )
            logger.info("Scheduled job '%s' registered: %s", name, cron_str)

    def _register_morning_briefing(self) -> None:
        self._register_cron_job("Morning Briefing", self._post_morning_briefing,
                                "morning_briefing", "0 8 * * 1-5")

    def _register_eod_review(self) -> None:
        self._register_cron_job("EOD Review", self._post_eod_review,
                                "eod_review", "0 17 * * 1-5")

    def _post_morning_briefing(self) -> None:
        """Post a morning briefing with today's calendar, overdue tasks, and pending Jira issues."""
        channel = self.config.get("jibsa", {}).get("channel_name", "jibsa")
        tz = self.config.get("jibsa", {}).get("timezone", "UTC")
        sections: list[str] = []

        # Calendar: today's events for all connected users
        google_users = self.credential_store.list_users_for_service("google")
        if google_users:
            cal_lines = []
            for uid in google_users:
                try:
                    creds = self.google_oauth.get_valid_credentials(uid)
                    if not creds:
                        continue
                    from .integrations.google_calendar_client import GoogleCalendarClient
                    client = GoogleCalendarClient(creds)
                    events = client.list_today_events(tz)
                    if events:
                        # Resolve user name
                        try:
                            user_info = self.slack.users_info(user=uid)
                            name = user_info["user"].get("real_name", uid)
                        except Exception:
                            name = uid
                        for ev in events:
                            summary = ev.get("summary", "(no title)")
                            start = ev.get("start", {})
                            time_str = start.get("dateTime", start.get("date", ""))
                            if "T" in time_str:
                                time_str = time_str.split("T")[1][:5]
                            cal_lines.append(f"  {time_str} {summary} ({name})")
                except Exception as e:
                    logger.warning("Morning briefing: calendar failed for %s: %s", uid, e)

            if cal_lines:
                sections.append("*Today's Calendar*\n" + "\n".join(cal_lines))

        # Notion: overdue tasks (per-user — aggregate from all connected users)
        notion_users = self.credential_store.list_users_for_service("notion")
        if notion_users:
            for uid in notion_users:
                try:
                    brain = build_user_second_brain(uid, self.notion_oauth, self.notion_user_registry, self.config)
                    if brain:
                        context = brain.get_context_for_request("overdue tasks")
                        if context:
                            name = uid  # could resolve to display name if needed
                            sections.append(f"*Overdue Tasks*\n{context[:1000]}")
                            break  # one Notion section is enough for the briefing
                except Exception as e:
                    logger.warning("Morning briefing: Notion query failed for %s: %s", uid, e)

        # Jira: assigned open issues
        if self.jira:
            try:
                issues = self.jira.search_issues(
                    "assignee = currentUser() AND resolution = Unresolved ORDER BY priority DESC",
                    max_results=10,
                )
                if issues:
                    jira_lines = []
                    for iss in issues:
                        key = iss.get("key", "")
                        summary = iss.get("fields", {}).get("summary", "")
                        jira_lines.append(f"  [{key}] {summary}")
                    sections.append("*Open Jira Issues*\n" + "\n".join(jira_lines))
            except Exception as e:
                logger.warning("Morning briefing: Jira query failed: %s", e)

        if not sections:
            sections.append("Nothing on the radar today — enjoy a clean slate!")

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "Good Morning! Here's your daily briefing"}},
        ]
        for section in sections:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": section}})
            blocks.append({"type": "divider"})

        text = "Morning Briefing\n" + "\n\n".join(sections)
        self._post_blocks(channel, None, blocks, text)
        logger.info("Morning briefing posted to #%s", channel)

    def _post_eod_review(self) -> None:
        """Post an end-of-day review with today's activity and tomorrow's schedule."""
        channel = self.config.get("jibsa", {}).get("channel_name", "jibsa")
        tz = self.config.get("jibsa", {}).get("timezone", "UTC")
        sections: list[str] = []

        # Approval history for today
        today_actions = [
            h for h in self._approval_history
            if h.get("timestamp", "").startswith(datetime.now().strftime("%Y-%m-%d"))
        ]
        if today_actions:
            action_lines = []
            for h in today_actions[-10:]:
                status_icon = "✅" if h["status"] == "approved" else "❌"
                action_lines.append(f"  {status_icon} {h.get('summary', 'Action')}")
            sections.append("*Today's Actions*\n" + "\n".join(action_lines))

        # Tomorrow's calendar
        google_users = self.credential_store.list_users_for_service("google")
        if google_users:
            cal_lines = []
            for uid in google_users:
                try:
                    creds = self.google_oauth.get_valid_credentials(uid)
                    if not creds:
                        continue
                    from .integrations.google_calendar_client import GoogleCalendarClient
                    client = GoogleCalendarClient(creds)
                    events = client.list_upcoming_events(days=2, timezone=tz)
                    # Filter to tomorrow only
                    tomorrow = (datetime.now(ZoneInfo(tz)) + timedelta(days=1)).date()
                    for ev in events:
                        start = ev.get("start", {})
                        date_str = start.get("dateTime", start.get("date", ""))
                        if str(tomorrow) in date_str:
                            summary = ev.get("summary", "(no title)")
                            time_str = date_str.split("T")[1][:5] if "T" in date_str else "all-day"
                            cal_lines.append(f"  {time_str} {summary}")
                except Exception as e:
                    logger.warning("EOD review: calendar failed for %s: %s", uid, e)

            if cal_lines:
                sections.append("*Tomorrow's Schedule*\n" + "\n".join(cal_lines))

        if not sections:
            sections.append("Quiet day — nothing to report. See you tomorrow!")

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "End of Day Review"}},
        ]
        for section in sections:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": section}})
            blocks.append({"type": "divider"})

        text = "EOD Review\n" + "\n\n".join(sections)
        self._post_blocks(channel, None, blocks, text)
        logger.info("EOD review posted to #%s", channel)

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

        # Priority 0.4: Pending Notion post-connect setup (user pasting parent page URL in DM)
        if user in self._pending_notion_setup and channel.startswith("D"):
            self._handle_notion_setup_response(channel, thread_ts, user, text)
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

        # Priority 1.5: Active SOP creation session
        if self.sop_flow.has_session(thread_ts):
            response = self.sop_flow.handle(thread_ts, user, text)
            self._post(channel, thread_ts, response)
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
            elif cmd == "audit":
                self._handle_audit(channel, thread_ts)
            elif cmd == "templates":
                self._handle_templates(channel, thread_ts)
            elif cmd in ("my connections", "connections"):
                self._handle_list_connections(channel, thread_ts, user)
            elif cmd in ("my schedules", "schedules"):
                self._handle_list_schedules(channel, thread_ts, user)
            else:
                self._handle_list_interns(channel, thread_ts)
            return

        # Schedule commands
        if cmd.startswith("schedule ") and not route.intern_name:
            self._handle_schedule(channel, thread_ts, user, route.message[9:].strip())
            return
        if cmd.startswith("remove schedule ") and not route.intern_name:
            self._handle_remove_schedule(channel, thread_ts, user, route.message[16:].strip())
            return
        if cmd.startswith("delete schedule ") and not route.intern_name:
            self._handle_remove_schedule(channel, thread_ts, user, route.message[16:].strip())
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
        if cmd.startswith("google token ") and not route.intern_name:
            self._handle_google_token(channel, thread_ts, user, cmd[13:].strip())
            return

        # SOP commands
        if (cmd.startswith("add sop") or cmd.startswith("create sop")) and not route.intern_name:
            self._handle_add_sop(channel, thread_ts, user, route.message)
            return
        if (cmd.startswith("show sop") or cmd.startswith("list sop")) and not route.intern_name:
            self._handle_list_sops(channel, thread_ts, route.message)
            return
        if (cmd.startswith("remove sop") or cmd.startswith("delete sop")) and not route.intern_name:
            self._handle_remove_sop(channel, thread_ts, route.message)
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
            # Check for "hire from template <name>" pattern
            msg_lower = route.message.lower()
            if "from template" in msg_lower:
                tmpl_name = msg_lower.split("from template", 1)[1].strip()
                self._handle_hire_from_template(channel, thread_ts, user, tmpl_name)
                return
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

    def _handle_templates(self, channel: str, thread_ts: str) -> None:
        """List available intern templates."""
        templates = list_templates()
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "Intern Templates"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "Ready-to-use interns. Hire one with `hire from template <name>`."}},
            {"type": "divider"},
        ]
        for tmpl in templates:
            tools_str = ", ".join(tmpl["tools"])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{tmpl['name']}* — {tmpl['role']}\n"
                        f"  Tools: {tools_str}\n"
                        f"  Responsibilities: {tmpl['responsibilities_count']} defined\n"
                        f"  _Hire: `hire from template {tmpl['key']}`_"
                    ),
                },
            })
        text = "Intern Templates\n" + "\n".join(f"- {t['name']} ({t['role']})" for t in templates)
        self._post_blocks(channel, thread_ts, blocks, text)

    def _handle_hire_from_template(self, channel: str, thread_ts: str, user: str, template_name: str) -> None:
        """Create an intern from a pre-built template."""
        tmpl = get_template(template_name)
        if not tmpl:
            available = ", ".join(t["key"] for t in list_templates())
            self._post(
                channel, thread_ts,
                f"No template named '{template_name}'. Available: {available}\n"
                f"Say `templates` to see details.",
            )
            return

        # Check if an intern with this name already exists
        if self.intern_registry.get_intern(tmpl["name"]):
            self._post(
                channel, thread_ts,
                f"An intern named '{tmpl['name']}' already exists. "
                f"Fire them first (`fire {tmpl['name'].lower()}`) or hire with a custom name.",
            )
            return

        jd = template_to_jd(tmpl, created_by=user)
        result = self.intern_registry.create_intern(jd)

        if result.get("ok"):
            self.router.update_names(self.intern_registry.get_intern_names())
            self.audit.log(
                action="intern_created",
                user_id=user,
                details={"name": jd.name, "role": jd.role, "template": template_name},
            )
            self._post(
                channel, thread_ts,
                f"✅ *{jd.name}* ({jd.role}) is ready!\n\n"
                f"Try: `@jibsa {jd.name.lower()} <your request>`\n"
                f"Say `show {jd.name.lower()}'s jd` to see the full Job Description.",
            )
        else:
            self._post(channel, thread_ts, f"⚠️ Failed to create intern: {result.get('error', 'unknown error')}")

    def _handle_fire_intern(self, channel: str, thread_ts: str, name: str) -> None:
        """Deactivate an intern."""
        result = self.intern_registry.deactivate_intern(name)
        if result.get("ok"):
            self.router.update_names(self.intern_registry.get_intern_names())
            self._post(channel, thread_ts, f"✅ Intern '{name}' has been deactivated.")
            self.audit.log(action="intern_deactivated", user_id=current_user_id.get(""), details={"name": name})
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
    # User schedules
    # ------------------------------------------------------------------

    _BUILTIN_JOBS = {
        "morning briefing": ("morning_briefing", "_post_morning_briefing"),
        "eod review": ("eod_review", "_post_eod_review"),
        "weekly digest": ("weekly_digest", "_post_activity_summary"),
    }

    def _handle_schedule(self, channel: str, thread_ts: str, user: str, text: str) -> None:
        """Create a user schedule from natural language."""
        lower = text.lower().strip()

        # Check for builtin job: "morning briefing 8:30am weekdays"
        for label, (job_name, handler_name) in self._BUILTIN_JOBS.items():
            if lower.startswith(label):
                time_spec = text[len(label):].strip()
                parsed = _parse_schedule_spec(time_spec)
                if not parsed:
                    self._post(channel, thread_ts, f"Couldn't parse the time. Try: `schedule {label} 8:30am weekdays`")
                    return

                cron, description = parsed
                # Remove existing schedule with same name for this user
                existing_id = self.schedule_store.remove_by_user_and_name(user, job_name)
                if existing_id:
                    self.reminder_scheduler.remove_cron_job(f"user_schedule_{existing_id}")

                sched = self.schedule_store.add(
                    user_id=user, name=job_name, schedule_type="builtin",
                    cron=cron, channel=channel,
                )
                handler = getattr(self, handler_name)
                self.reminder_scheduler.add_cron_job(
                    f"user_schedule_{sched['id']}", handler, cron,
                )
                self.audit.log(action="schedule_created", user_id=user, details={"name": job_name, "cron": cron})
                self._post(channel, thread_ts, f"Scheduled *{label}* at {description}.")
                return

        # Custom recurring: "every monday 9am: check sprint board"
        import re
        # Split on colon to separate schedule spec from message
        match = re.match(r"(.+?):\s*(.+)", text, re.DOTALL)
        if match:
            spec_part = match.group(1).strip()
            message = match.group(2).strip()
        else:
            self._post(
                channel, thread_ts,
                "For custom schedules, use: `schedule every monday 9am: your message here`\n"
                "For built-in jobs: `schedule morning briefing 8:30am weekdays`"
            )
            return

        parsed = _parse_schedule_spec(spec_part)
        if not parsed:
            self._post(channel, thread_ts, f"Couldn't parse the schedule. Try: `schedule every monday 9am: your message`")
            return

        cron, description = parsed
        # Use first few words of message as the name
        name = re.sub(r"[^a-z0-9 ]", "", message.lower())[:40].strip().replace(" ", "_") or "custom"

        sched = self.schedule_store.add(
            user_id=user, name=name, schedule_type="custom",
            cron=cron, channel=channel, message=message,
        )
        self.reminder_scheduler.add_cron_job(
            f"user_schedule_{sched['id']}",
            self.reminder_scheduler.fire_custom_schedule,
            cron,
            channel=channel,
            message=message,
        )
        self.audit.log(action="schedule_created", user_id=user, details={"name": name, "cron": cron, "message": message})
        self._post(channel, thread_ts, f"Scheduled *{message}* at {description}.")

    def _handle_remove_schedule(self, channel: str, thread_ts: str, user: str, text: str) -> None:
        """Remove a user schedule by name or number."""
        schedules = self.schedule_store.list_for_user(user)
        if not schedules:
            self._post(channel, thread_ts, "You have no schedules.")
            return

        target = text.strip()

        # Try by number (1-indexed from "my schedules" list)
        try:
            idx = int(target) - 1
            if 0 <= idx < len(schedules):
                sched = schedules[idx]
                self.schedule_store.remove(sched["id"])
                self.reminder_scheduler.remove_cron_job(f"user_schedule_{sched['id']}")
                display_name = sched["name"].replace("_", " ")
                self.audit.log(action="schedule_removed", user_id=user, details={"name": sched["name"]})
                self._post(channel, thread_ts, f"Removed schedule *{display_name}*.")
                return
        except ValueError:
            pass

        # Try by name (exact or fuzzy match)
        target_lower = target.lower().replace(" ", "_")
        for sched in schedules:
            if sched["name"] == target_lower or sched["name"].replace("_", " ") == target.lower():
                self.schedule_store.remove(sched["id"])
                self.reminder_scheduler.remove_cron_job(f"user_schedule_{sched['id']}")
                display_name = sched["name"].replace("_", " ")
                self.audit.log(action="schedule_removed", user_id=user, details={"name": sched["name"]})
                self._post(channel, thread_ts, f"Removed schedule *{display_name}*.")
                return

        self._post(channel, thread_ts, f"No schedule matching '{target}'. Say `my schedules` to see your list.")

    def _handle_list_schedules(self, channel: str, thread_ts: str, user: str) -> None:
        """List the user's schedules."""
        schedules = self.schedule_store.list_for_user(user)
        if not schedules:
            self._post(
                channel, thread_ts,
                "You have no schedules.\n\n"
                "Set one up:\n"
                "  `schedule morning briefing 8:30am weekdays`\n"
                "  `schedule every monday 9am: check sprint board`"
            )
            return

        lines = ["*Your Schedules*\n"]
        for i, s in enumerate(schedules, 1):
            name = s["name"].replace("_", " ")
            cron = s["cron"]
            stype = s["schedule_type"]
            msg = f" — _{s['message'][:50]}_" if s.get("message") else ""
            lines.append(f"  {i}. *{name}* (`{cron}`){msg}")
        lines.append("\n_Say `remove schedule <name or number>` to delete._")
        self._post(channel, thread_ts, "\n".join(lines))

    def _register_user_schedules(self) -> None:
        """On startup, re-register all user schedules with APScheduler."""
        schedules = self.schedule_store.list_all_enabled()
        if not schedules:
            return

        count = 0
        for sched in schedules:
            job_id = f"user_schedule_{sched['id']}"
            try:
                if sched["schedule_type"] == "builtin":
                    handler_map = {
                        "morning_briefing": self._post_morning_briefing,
                        "eod_review": self._post_eod_review,
                        "weekly_digest": self._post_activity_summary,
                    }
                    handler = handler_map.get(sched["name"])
                    if handler:
                        self.reminder_scheduler.add_cron_job(job_id, handler, sched["cron"])
                        count += 1
                elif sched["schedule_type"] == "custom":
                    self.reminder_scheduler.add_cron_job(
                        job_id,
                        self.reminder_scheduler.fire_custom_schedule,
                        sched["cron"],
                        channel=sched["channel"],
                        message=sched["message"],
                    )
                    count += 1
            except Exception as e:
                logger.warning("Failed to re-register schedule %s: %s", sched["id"], e)

        if count:
            logger.info("Re-registered %d user schedule(s) from DB", count)

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
                        "*SOPs (Standard Operating Procedures)*\n"
                        "`add sop` — Create a shared SOP\n"
                        "`add sop for <name>` — Create an intern-specific SOP\n"
                        "`show sops` — List all SOPs\n"
                        "`show sop <name>` — View SOP details\n"
                        "`remove sop <name>` — Delete a SOP"
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
        """Record a completed/rejected plan to history and audit log."""
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

        # Audit log
        action = "plan_approved" if status == "approved" else "plan_rejected"
        self.audit.log(
            action=action,
            user_id=current_user_id.get(""),
            details={"summary": plan.get("summary", ""), "steps": len(plan.get("steps", []))},
        )

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
        """Fetch Notion context with circuit breaker protection (per-user)."""
        user_id = current_user_id.get()
        if not user_id:
            return ""
        brain = self._get_notion_brain_for_step()
        if not brain:
            return ""
        try:
            self._notion_circuit.check()
            context = brain.get_context_for_request(text)
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
    # SOP management commands
    # ------------------------------------------------------------------

    def _handle_add_sop(self, channel: str, thread_ts: str, user: str, text: str) -> None:
        """Start a conversational SOP creation flow."""
        # Parse: "add sop for <intern>" or "add sop" (shared)
        msg_lower = text.lower()
        intern_name = None

        # Extract intern name from "add sop for alex" or "create sop for alex"
        for prefix in ("add sop for ", "create sop for "):
            if msg_lower.startswith(prefix):
                intern_name = text[len(prefix):].strip()
                if intern_name and not self.intern_registry.get_intern(intern_name):
                    self._post(
                        channel, thread_ts,
                        f"No intern named '{intern_name}'. "
                        f"Available: {', '.join(self.intern_registry.get_intern_names()) or 'none'}",
                    )
                    return
                break

        self.sop_flow.start_session(thread_ts, user, text, intern_name=intern_name)
        scope = f"for *{intern_name}*" if intern_name else "(shared, available to all interns)"
        self._post(
            channel, thread_ts,
            f"Let's create a new SOP {scope}. "
            f"What should this SOP do? Give me a name and description to get started.",
        )

    def _handle_list_sops(self, channel: str, thread_ts: str, text: str) -> None:
        """List SOPs — all, for an intern, or show a specific SOP."""
        msg_lower = text.lower().strip()

        # "show sop <name>" — single SOP detail
        for prefix in ("show sop ", "list sop "):
            if msg_lower.startswith(prefix):
                remainder = text[len(prefix):].strip()

                # "show sops for alex"
                if remainder.lower().startswith("for "):
                    intern_name = remainder[4:].strip()
                    sops = self.sop_registry.list_sops_for_intern(intern_name)
                    if not sops:
                        self._post(channel, thread_ts, f"No SOPs found for '{intern_name}'.")
                        return
                    self._post_sop_list(channel, thread_ts, sops, f"SOPs for {intern_name}")
                    return

                # Try as SOP name
                if remainder.lower() not in ("", "s"):
                    sop_name = remainder.rstrip("s")  # handle "show sops"
                    sop = self.sop_registry.get_sop_by_name(sop_name)
                    if not sop:
                        # Try intern-scoped
                        for intern in self.intern_registry.list_interns():
                            sop = self.sop_registry.get_sop_by_name(sop_name, intern.name)
                            if sop:
                                break
                    if sop:
                        self._post(channel, thread_ts, sop.format_sop())
                        return
                    self._post(channel, thread_ts, f"No SOP named '{sop_name}' found.")
                    return

        # Default: list all SOPs
        sops = self.sop_registry.list_all_sops()
        if not sops:
            self._post(channel, thread_ts, "No SOPs yet. Say `add sop` to create one.")
            return
        self._post_sop_list(channel, thread_ts, sops, "All SOPs")

    def _handle_remove_sop(self, channel: str, thread_ts: str, text: str) -> None:
        """Remove a SOP by name."""
        for prefix in ("remove sop ", "delete sop "):
            if text.lower().startswith(prefix):
                sop_name = text[len(prefix):].strip()
                break
        else:
            self._post(channel, thread_ts, "Usage: `remove sop <name>`")
            return

        # Find the SOP — try shared first, then intern-scoped
        sop = self.sop_registry.get_sop_by_name(sop_name)
        if not sop:
            for intern in self.intern_registry.list_interns():
                sop = self.sop_registry.get_sop_by_name(sop_name, intern.name)
                if sop:
                    break

        if not sop:
            self._post(channel, thread_ts, f"No SOP named '{sop_name}' found.")
            return

        result = self.sop_registry.delete_sop(sop.id)
        if result.get("ok"):
            self._post(channel, thread_ts, f"✅ SOP '{sop_name}' removed.")
        else:
            self._post(channel, thread_ts, f"⚠️ Failed to remove SOP: {result.get('error')}")

    def _post_sop_list(self, channel: str, thread_ts: str, sops: list, title: str) -> None:
        """Post a formatted list of SOPs."""
        lines = [f"*{title}* ({len(sops)} total)\n"]
        for sop in sops:
            scope = f"_{sop.intern_id}_" if sop.intern_id else "_shared_"
            keywords = ", ".join(sop.trigger_keywords[:5])
            lines.append(
                f"  `{sop.name}` ({scope}) — {keywords}"
                f"{' ...' if len(sop.trigger_keywords) > 5 else ''}"
            )
        lines.append(f"\nSay `show sop <name>` for details.")
        self._post(channel, thread_ts, "\n".join(lines))

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
        # If intern has delegate tool, list available colleagues
        delegate_section = ""
        if "delegate" in [t.lower() for t in intern.tools_allowed]:
            other_interns = [
                i for i in self.intern_registry.list_interns()
                if i.name.lower() != intern.name.lower()
            ]
            if other_interns:
                colleague_list = "\n".join(
                    f"- {i.name} ({i.role}) — tools: {', '.join(i.tools_allowed)}"
                    for i in other_interns
                )
                delegate_section = (
                    f"\n\nYou can delegate subtasks to other interns using the "
                    f"\"Delegate to Intern\" tool. Available colleagues:\n{colleague_list}\n"
                    f"Use delegation when you need expertise or tools you don't have."
                )

        backstory = (
            f"You are {intern.name}, a {intern.role}.\n\n"
            f"Responsibilities:\n{responsibilities}\n\n"
            f"Communication style: {intern.tone}\n\n"
            f"Autonomy rules: {intern.autonomy_rules}\n\n"
            f"Tools available:\n{tools_desc}\n\n"
            f"IMPORTANT: For write operations (creating/updating tasks, projects, notes, etc.), "
            f"respond with ONLY a JSON action plan. For questions and read-only requests, answer directly."
            f"{delegate_section}"
        )

        # Get CrewAI tools for this intern
        crewai_tools = self.tool_registry.get_crewai_tools_for_intern(intern)

        # Memory context
        memory_context = intern.get_memory_context(channel=channel)

        # SOP resolution — find matching SOP for this intern + message
        sop = self.sop_registry.resolve_sops(intern.name, text)
        if sop:
            # Validate SOP tools vs intern's allowed tools
            missing_tools = [t for t in sop.tools_required if t not in intern.tools_allowed]
            if missing_tools:
                logger.warning(
                    "SOP '%s' requires tools %s not in intern '%s' — falling back to freeform",
                    sop.name, missing_tools, intern.name,
                )
                sop = None
            else:
                logger.info("Using SOP '%s' for intern '%s'", sop.name, intern.name)

        thinking_ts = self._post_thinking(channel, thread_ts)

        start = time.monotonic()
        if sop:
            response = self.runner.run_for_intern_with_sop(
                user_message=text,
                intern_name=intern.name,
                intern_role=intern.role,
                intern_backstory=backstory,
                sop=sop,
                tools=crewai_tools,
                notion_context=notion_context,
                history=history,
                memory_context=memory_context,
                active_integrations=active,
            )
        else:
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
            elif service == "notion":
                notion_brain = self._get_notion_brain_for_step()
                if not notion_brain:
                    result = {"ok": False, "error": "Notion is not connected. Say `connect notion` first."}
                else:
                    try:
                        self._notion_circuit.check()
                        result = notion_brain.execute_step(step)
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
            elif service == "calendar":
                result = self._execute_google_step(step, "calendar")
            elif service == "gmail":
                result = self._execute_google_step(step, "gmail")
            elif service == "drive":
                result = self._execute_google_step(step, "drive")
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

        # Audit: plan executed
        ok_count = sum(1 for _, r in results if r.get("ok"))
        self.audit.log(
            action="plan_executed",
            user_id=current_user_id.get(""),
            details={"summary": plan.get("summary", ""), "steps": len(results), "ok": ok_count},
            status="ok" if ok_count == len(results) else "partial",
            thread_ts=thread_ts,
        )

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
    # Google Calendar / Gmail execution
    # ------------------------------------------------------------------

    def _get_notion_brain_for_step(self):
        """Resolve the per-user Notion brain for step execution."""
        user_id = current_user_id.get()
        if not user_id:
            return None
        try:
            brain = build_user_second_brain(
                user_id=user_id,
                notion_oauth=self.notion_oauth,
                user_registry=self.notion_user_registry,
                config=self.config,
            )
            return brain
        except Exception as e:
            logger.warning("Failed to build per-user Notion brain for %s: %s", user_id, e)
            return None

    def _execute_google_step(self, step: dict, service: str) -> dict:
        """Execute a Google Calendar or Gmail step using per-user credentials."""
        user_id = current_user_id.get()
        if not user_id:
            return {"ok": False, "error": "Could not determine the requesting user."}

        creds = self.google_oauth.get_valid_credentials(user_id)
        if not creds:
            return {"ok": False, "error": f"Google not connected. Say `connect google` first."}

        try:
            self._google_circuit.check()
            if service == "calendar":
                from .integrations.google_calendar_client import GoogleCalendarClient
                client = GoogleCalendarClient(creds)
            elif service == "gmail":
                from .integrations.gmail_client import GmailClient
                client = GmailClient(creds)
            else:
                from .integrations.google_drive_client import GoogleDriveClient
                client = GoogleDriveClient(creds)

            result = client.execute_step(step)
            self._google_circuit.record_success()
            return result
        except CircuitOpenError:
            return {"ok": False, "error": "Google APIs are temporarily unavailable (circuit open). Try again later."}
        except Exception as e:
            self._google_circuit.record_failure()
            return {"ok": False, "error": f"Google {service} error: {e}"}

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

    _SUPPORTED_SERVICES = {"google", "notion"}

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
                        "*Connect Google Workspace*\n\n"
                        "1. Click the link below to authorize Jibsa:\n"
                        f"   {auth_url}\n\n"
                        "2. After clicking *Allow*, your browser will redirect to a page that won't load — that's expected.\n"
                        "3. Copy the `code` value from the URL bar. It looks like:\n"
                        "   `http://localhost?code=`*`4/0Axxxxxx...`*`&scope=...`\n\n"
                        "4. *Paste just the code value here* (in this DM).\n\n"
                        "_This link expires in a few minutes._"
                    ),
                )
                self._pending_oauth[user] = (service, dm_channel, thread_ts)
                self._post(channel, thread_ts, "I've sent you a DM with the authorization link. Follow the steps there.")
            except Exception as e:
                logger.error("Failed to DM user %s: %s", user, e)
                self._post(channel, thread_ts, f"Couldn't send you a DM. Error: {e}")

        elif service == "notion":
            if not self.notion_oauth.is_configured:
                self._post(channel, thread_ts, "Notion OAuth is not configured. Set `NOTION_OAUTH_CLIENT_ID` and `NOTION_OAUTH_CLIENT_SECRET` in `.env`.")
                return

            existing = self.credential_store.list_services(user)
            if "notion" in existing:
                self._post(channel, thread_ts, "You're already connected to Notion. Say `disconnect notion` first if you want to reconnect.")
                return

            auth_url = self.notion_oauth.generate_auth_url()
            if not auth_url:
                self._post(channel, thread_ts, "Failed to generate authorization URL.")
                return

            try:
                dm = self.slack.conversations_open(users=user)
                dm_channel = dm["channel"]["id"]
                self.slack.chat_postMessage(
                    channel=dm_channel,
                    text=(
                        "*Connect Notion*\n\n"
                        "1. Click the link below to authorize Jibsa:\n"
                        f"   {auth_url}\n\n"
                        "2. Select the pages you want to share, then click *Allow access*.\n"
                        "3. Your browser will redirect to a page that won't load — that's expected.\n"
                        "4. Copy the `code` value from the URL bar. It looks like:\n"
                        "   `http://localhost?code=`*`abc123...`*`&state=...`\n\n"
                        "5. *Paste just the code value here* (in this DM).\n\n"
                        "_This link expires in a few minutes._"
                    ),
                )
                self._pending_oauth[user] = (service, dm_channel, thread_ts)
                self._post(channel, thread_ts, "I've sent you a DM with the authorization link. Follow the steps there.")
            except Exception as e:
                logger.error("Failed to DM user %s: %s", user, e)
                self._post(channel, thread_ts, f"Couldn't send you a DM. Error: {e}")

    def _handle_oauth_code(self, channel: str, thread_ts: str, user: str, text: str) -> None:
        """Handle an OAuth authorization code pasted in DM."""
        service, orig_dm_channel, orig_thread_ts = self._pending_oauth.pop(user)

        # Extract just the code — user might paste full URL or just the code
        # Slack auto-links URLs as <http://...> and encodes & as &amp;
        import html as _html
        from urllib.parse import urlparse, parse_qs, unquote
        raw = _html.unescape(text.strip().strip("<>"))
        if "code=" in raw:
            parsed = urlparse(raw)
            code = parse_qs(parsed.query).get("code", [""])[0]
        else:
            code = raw
        code = unquote(code).strip()
        logger.info("OAuth extracted code for user=%s: %s...", user, code[:20])

        if service == "google":
            result = self.google_oauth.exchange_code(user, code)
            if result["ok"]:
                self._post(channel, thread_ts, "Connected to Google Workspace! Calendar, Gmail, and Drive are now available to your interns.")
                self.audit.log(action="service_connected", user_id=user, service="google")
            else:
                self._post(channel, thread_ts, f"Authorization failed: {result['error']}\n\nSay `connect google` to try again.")

        elif service == "notion":
            result = self.notion_oauth.exchange_code(user, code)
            if result["ok"]:
                workspace = result.get("workspace_name", "your workspace")
                # Discover databases
                db_names = []
                try:
                    brain = build_user_second_brain(
                        user_id=user,
                        notion_oauth=self.notion_oauth,
                        user_registry=self.notion_user_registry,
                        config=self.config,
                    )
                    if brain:
                        db_names = [db["name"] for db in brain._registry.all_databases()]
                except Exception as e:
                    logger.warning("Notion discovery failed for user=%s: %s", user, e)

                self.audit.log(action="service_connected", user_id=user, service="notion")

                # Post-connect setup: show what we found and ask for parent page
                db_list = "\n".join(f"  - {name}" for name in db_names) if db_names else "  _(none found)_"
                self._post(
                    channel, thread_ts,
                    f"Connected to Notion workspace *{workspace}*!\n\n"
                    f"*Databases discovered:*\n{db_list}\n\n"
                    f"*Optional: set a parent page*\n"
                    f"A parent page lets me auto-create new databases (e.g. Tasks, Projects) when needed.\n"
                    f"Paste a Notion page URL here, or say *skip* to finish setup."
                )
                self._pending_notion_setup[user] = channel
            else:
                self._post(channel, thread_ts, f"Authorization failed: {result['error']}\n\nSay `connect notion` to try again.")

    def _handle_notion_setup_response(self, channel: str, thread_ts: str, user: str, text: str) -> None:
        """Handle user response during Notion post-connect setup (parent page URL or skip)."""
        self._pending_notion_setup.pop(user, None)

        clean = text.strip().lower()
        if clean in ("skip", "no", "none", "done"):
            self._post(channel, thread_ts, "Notion setup complete! Your databases are ready to use.")
            return

        # Try to extract a Notion page ID from the pasted URL or ID
        import re as _re
        from urllib.parse import urlparse

        raw = text.strip().strip("<>")  # strip Slack URL formatting
        # Match UUID pattern (with or without dashes)
        match = _re.search(r"([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})", raw, _re.IGNORECASE)

        if not match:
            # Try to extract from Notion URL slug (last 32 hex chars before query)
            slug_match = _re.search(r"([0-9a-f]{32})(?:\?|$)", raw, _re.IGNORECASE)
            if slug_match:
                hex_id = slug_match.group(1)
                page_id = f"{hex_id[:8]}-{hex_id[8:12]}-{hex_id[12:16]}-{hex_id[16:20]}-{hex_id[20:]}"
            else:
                self._post(channel, thread_ts, "Couldn't find a page ID in that. Paste a Notion page URL (e.g. `https://notion.so/My-Page-abc123...`), or say *skip*.")
                self._pending_notion_setup[user] = channel  # keep waiting
                return
        else:
            page_id = match.group(1)

        # Verify access to the page
        try:
            token = self.notion_oauth.get_token(user)
            if not token:
                self._post(channel, thread_ts, "Notion setup complete! (Could not verify page — token not found)")
                return

            from .integrations.notion_client import NotionClient, NotionAPIError
            client = NotionClient(token)
            page = client.get_page(page_id)
            # Extract title
            title_parts = page.get("properties", {}).get("title", {}).get("title", [])
            page_title = "".join(t.get("plain_text", "") for t in title_parts) if title_parts else "(untitled)"

            # Store the parent page ID
            self.notion_user_registry.set_parent_page_id(user, page_id)

            # Discover child databases under this page
            from .integrations.notion_second_brain import _discover_child_databases
            from .integrations.notion_db_registry import DatabaseRegistry
            from .integrations.notion_db_templates import DB_TEMPLATES

            discovered = _discover_child_databases(client, page_id)
            if discovered:
                registry = self.notion_user_registry.get_registry(user) or DatabaseRegistry()
                new_dbs = []
                for db in discovered:
                    if not registry.get_db_id(db["name"]):
                        template = DB_TEMPLATES.get(db["name"], {})
                        keywords = template.get("keywords", [])
                        registry.register(db["name"], db["id"], keywords)
                        new_dbs.append(db["name"])
                if new_dbs:
                    self.notion_user_registry.save_registry(user, registry)

                db_list = "\n".join(f"  - {db['name']}" for db in discovered)
                self._post(
                    channel, thread_ts,
                    f"Parent page set to *{page_title}* (`{page_id[:8]}...`)\n\n"
                    f"*Child databases found:*\n{db_list}\n\n"
                    f"Notion setup complete! New databases will be auto-created under this page when needed."
                )
            else:
                self._post(
                    channel, thread_ts,
                    f"Parent page set to *{page_title}* (`{page_id[:8]}...`)\n\n"
                    f"No child databases found under this page yet — they'll be auto-created when needed.\n\n"
                    f"Notion setup complete!"
                )

        except NotionAPIError as e:
            self._post(
                channel, thread_ts,
                f"Couldn't access that page — make sure you shared it with the Jibsa integration during authorization.\n"
                f"Error: {e}\n\n"
                f"Paste another URL, or say *skip* to finish without a parent page."
            )
            self._pending_notion_setup[user] = channel  # keep waiting
        except Exception as e:
            logger.error("Notion setup page verification failed for user=%s: %s", user, e)
            self._post(channel, thread_ts, f"Something went wrong: {e}\n\nSay *skip* to finish setup, or paste another URL.")
            self._pending_notion_setup[user] = channel  # keep waiting

    def _handle_google_token(self, channel: str, thread_ts: str, user: str, raw: str) -> None:
        """Store Google credentials from a token JSON pasted by the user."""
        import json as _json
        import re as _re
        # Strip Slack mention/formatting
        text = _re.sub(r"^<@[A-Z0-9]+>\s*", "", raw.strip())
        try:
            token_data = _json.loads(text)
        except _json.JSONDecodeError:
            self._post(channel, thread_ts, "Couldn't parse that — make sure you pasted the full JSON output from `scripts/google_auth.py`.")
            return
        required = {"access_token", "refresh_token", "client_id", "client_secret"}
        missing = required - set(token_data.keys())
        if missing:
            self._post(channel, thread_ts, f"Token JSON is missing fields: {', '.join(missing)}")
            return
        self.credential_store.set(user, "google", token_data)
        self.audit.log(action="service_connected", user_id=user, service="google")
        self._post(channel, thread_ts, "Connected to Google Workspace! Calendar, Gmail, and Drive are now available to your interns.")

    def _handle_disconnect(self, channel: str, thread_ts: str, user: str, service: str) -> None:
        """Revoke and delete stored credentials for a service."""
        if service not in self._SUPPORTED_SERVICES:
            self._post(channel, thread_ts, f"Unknown service '{service}'. Supported: {', '.join(sorted(self._SUPPORTED_SERVICES))}")
            return

        if service == "google":
            result = self.google_oauth.revoke_and_delete(user)
            if result["ok"]:
                self._post(channel, thread_ts, "Disconnected from Google. Your credentials have been revoked and deleted.")
                self.audit.log(action="service_disconnected", user_id=user, service="google")
            else:
                self._post(channel, thread_ts, f"Could not disconnect: {result['error']}")

        elif service == "notion":
            result = self.notion_oauth.revoke_and_delete(user)
            if result["ok"]:
                self.notion_user_registry.delete_registry(user)
                self._post(channel, thread_ts, "Disconnected from Notion. Your credentials and database registry have been deleted.\n_To fully revoke access, remove Jibsa from your Notion integrations at notion.so/my-integrations._")
                self.audit.log(action="service_disconnected", user_id=user, service="notion")
            else:
                self._post(channel, thread_ts, f"Could not disconnect: {result['error']}")

    def _handle_audit(self, channel: str, thread_ts: str) -> None:
        """Show recent audit log entries."""
        entries = self.audit.query(limit=15)
        if not entries:
            self._post(channel, thread_ts, "No audit entries yet.")
            return

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": "Audit Log (recent)"}},
        ]
        for entry in entries:
            ts = entry.get("timestamp", "")
            action = entry.get("action", "")
            service = entry.get("service", "")
            status = entry.get("status", "ok")
            details = entry.get("details", {})
            summary = details.get("summary", "") if isinstance(details, dict) else ""
            user_id = entry.get("user_id", "")

            icon = "✅" if status == "ok" else "⚠️"
            svc_str = f" ({service})" if service else ""
            user_str = f" by <@{user_id}>" if user_id else ""
            detail_str = f" — {summary}" if summary else ""

            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"{icon} `{ts}` *{action}*{svc_str}{user_str}{detail_str}"},
            })

        text = "Audit Log\n" + "\n".join(
            f"{e.get('timestamp')} {e.get('action')} {e.get('status')}" for e in entries
        )
        self._post_blocks(channel, thread_ts, blocks, text)

    def _handle_list_connections(self, channel: str, thread_ts: str, user: str) -> None:
        """List the user's connected services."""
        services = self.credential_store.list_services(user)
        # Filter out internal registry entries
        display_services = [s for s in services if not s.endswith("_registry")]
        if not display_services:
            self._post(channel, thread_ts, "You have no connected services. Say `connect google` or `connect notion` to get started.")
            return

        lines = ["*Your Connected Services*\n"]
        for svc in display_services:
            extra = ""
            if svc == "notion":
                ws = self.notion_oauth.get_workspace_name(user)
                if ws:
                    extra = f" (workspace: {ws})"
            lines.append(f"  - {svc}{extra}")
        lines.append("\n_Say `disconnect <service>` to remove a connection._")
        self._post(channel, thread_ts, "\n".join(lines))


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
