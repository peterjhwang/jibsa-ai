"""
CrewRunner — creates CrewAI Agent + Task + Crew per request.

Each user request spawns a one-shot Crew:
- The Agent is built from InternJD (or Jibsa's persona for orchestrator requests).
- Read-only tools (Notion query, web search) execute during reasoning.
- Write operations are proposed as JSON action_plans → Slack approve flow.

Supports multiple LLM providers via CrewAI's native integration:
  "anthropic/claude-sonnet-4-20250514", "openai/gpt-4o", "google/gemini-2-0-flash", etc.
"""
import json
import logging
import re
import signal
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from crewai import Agent, Crew, Process, Task

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _load_text(path: Path) -> str:
    with open(path) as f:
        return f.read()


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from text, stripping markdown code fences if present."""
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _build_llm_string(config: dict) -> str:
    """Build CrewAI LLM string from config. Format: 'provider/model'."""
    llm_config = config.get("llm", {})
    provider = llm_config.get("provider", "anthropic")
    model = llm_config.get("model", "claude-sonnet-4-20250514")

    # CrewAI expects "provider/model" format
    if "/" in model:
        return model  # already in provider/model format
    return f"{provider}/{model}"


class CrewRunner:
    """Creates and runs CrewAI Crews for each request."""

    def __init__(self, config: dict):
        self.config = config
        self._persona = _load_yaml(_CONFIG_DIR / "persona.yaml")["persona"]
        self._llm_string = _build_llm_string(config)
        self._temperature = config.get("llm", {}).get("temperature", 0.7)
        self._max_tokens = config.get("llm", {}).get("max_tokens", 4096)
        jibsa_cfg = config.get("jibsa", {})
        self._max_iter: int = jibsa_cfg.get("crew_max_iter", 10)
        self._crew_timeout: int = jibsa_cfg.get("crew_timeout", 300)

    def run_for_jibsa(
        self,
        user_message: str,
        tools: list | None = None,
        notion_context: str = "",
        history: list[dict] | None = None,
        active_integrations: list[str] | None = None,
    ) -> dict | str:
        """Run a request as Jibsa (the orchestrator persona)."""
        tz = self.config.get("jibsa", {}).get("timezone", "UTC")
        now = datetime.now()

        integration_lines = (
            "\n".join(f"- {i}" for i in (active_integrations or []))
            if active_integrations
            else "No integrations connected yet."
        )

        history_text = self._format_history(history)
        context_section = (
            f"\n\nNotion Context (live data from user's Second Brain):\n{notion_context}"
            if notion_context else ""
        )

        backstory = (
            f"{self._persona['tone'].strip()}\n\n"
            f"Boundaries:\n{self._persona['boundaries'].strip()}\n\n"
            f"Connected integrations:\n{integration_lines}\n\n"
            f"Today: {now.strftime('%A, %B %d, %Y')} {now.strftime('%H:%M')} {tz}\n\n"
            f"Conversation history:\n{history_text}"
            f"{context_section}"
        )

        agent = Agent(
            role=f"{self._persona['name']} — AI Steward (집사)",
            goal=(
                "Help the user by answering questions, managing tasks, and coordinating work. "
                "If a request is ambiguous or missing critical details, ask a brief clarifying question "
                "before acting. For write operations, ALWAYS respond with a JSON action plan for approval."
            ),
            backstory=backstory,
            tools=tools or [],
            llm=self._llm_string,
            verbose=False,
            memory=True,
            max_iter=self._max_iter,
        )

        return self._run_crew(agent, user_message)

    def run_for_intern(
        self,
        user_message: str,
        intern_name: str,
        intern_role: str,
        intern_backstory: str,
        tools: list | None = None,
        notion_context: str = "",
        history: list[dict] | None = None,
        memory_context: str = "",
        active_integrations: list[str] | None = None,
    ) -> dict | str:
        """Run a request as a specific intern."""
        tz = self.config.get("jibsa", {}).get("timezone", "UTC")
        now = datetime.now()

        history_text = self._format_history(history)
        context_section = (
            f"\n\nNotion Context (live data):\n{notion_context}"
            if notion_context else ""
        )
        memory_section = f"\n\n{memory_context}" if memory_context else ""

        integration_lines = (
            "\n".join(f"- {i}" for i in (active_integrations or []))
            if active_integrations
            else "No integrations connected."
        )

        backstory = (
            f"{intern_backstory}\n\n"
            f"Connected integrations:\n{integration_lines}\n\n"
            f"Today: {now.strftime('%A, %B %d, %Y')} {now.strftime('%H:%M')} {tz}\n\n"
            f"Conversation history:\n{history_text}"
            f"{context_section}"
            f"{memory_section}"
        )

        agent = Agent(
            role=f"{intern_name} — {intern_role}",
            goal=(
                f"Complete tasks as {intern_name}, a {intern_role}. "
                "If a request is ambiguous or missing critical details (e.g. no deadline, unclear scope, "
                "multiple interpretations), ask a brief clarifying question before proposing an action. "
                "For any operation that modifies external state (creating tasks, updating records, etc.), "
                "respond with a JSON action plan for approval. "
                "For read-only queries and conversations, respond directly."
            ),
            backstory=backstory,
            tools=tools or [],
            llm=self._llm_string,
            verbose=False,
            memory=True,
            max_iter=self._max_iter,
        )

        return self._run_crew(agent, user_message)

    def run_for_hire(
        self,
        user_message: str,
        available_tools: str,
        history: list[dict] | None = None,
    ) -> str:
        """Run a hire flow conversation. Always returns str."""
        history_text = self._format_history(history)
        now = datetime.now()

        agent = Agent(
            role="Jibsa — Intern Hiring Manager",
            goal=(
                "Help the user create a new AI intern by gathering all required Job Description fields. "
                "When you have enough information, output ONLY a JSON object with type: intern_jd."
            ),
            backstory=(
                f"You are Jibsa (집사), helping create a new AI intern.\n\n"
                f"Required JD fields: Name, Role, Responsibilities (list), Tone, "
                f"Tools Allowed (from: {available_tools}), Autonomy Rules.\n\n"
                f"Ask clarifying questions naturally. When complete, output JSON:\n"
                f'{{"type": "intern_jd", "name": "...", "role": "...", '
                f'"responsibilities": [...], "tone": "...", "tools_allowed": [...], '
                f'"autonomy_rules": "..."}}\n\n'
                f"Today: {now.strftime('%A, %B %d, %Y')}\n\n"
                f"Conversation so far:\n{history_text}"
            ),
            tools=[],
            llm=self._llm_string,
            verbose=False,
            memory=False,
            max_iter=5,
        )

        task = Task(
            description=user_message,
            expected_output="Either clarifying questions or a complete intern_jd JSON",
            agent=agent,
        )

        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
        )

        try:
            result = crew.kickoff()
            return str(result.raw) if hasattr(result, "raw") else str(result)
        except Exception as e:
            logger.error("Hire crew failed: %s", e)
            return f"⚠️ Something went wrong: {e}"

    def run_for_team(
        self,
        user_message: str,
        team: list[dict],  # list of {"name": str, "role": str, "backstory": str, "tools": list}
        notion_context: str = "",
        history: list[dict] | None = None,
        active_integrations: list[str] | None = None,
    ) -> dict | str:
        """Run a multi-agent crew with the given team members."""
        tz = self.config.get("jibsa", {}).get("timezone", "UTC")
        now = datetime.now()
        history_text = self._format_history(history)

        context_section = (
            f"\n\nNotion Context (live data):\n{notion_context}"
            if notion_context else ""
        )
        integration_lines = (
            "\n".join(f"- {i}" for i in (active_integrations or []))
            if active_integrations
            else "No integrations connected."
        )

        agents = []
        for member in team:
            backstory = (
                f"{member['backstory']}\n\n"
                f"Connected integrations:\n{integration_lines}\n\n"
                f"Today: {now.strftime('%A, %B %d, %Y')} {now.strftime('%H:%M')} {tz}\n\n"
                f"Conversation history:\n{history_text}"
                f"{context_section}"
            )
            agent = Agent(
                role=f"{member['name']} — {member['role']}",
                goal=(
                    f"Contribute to the team task as {member['name']}, a {member['role']}. "
                    "Collaborate with your teammates to produce the best result. "
                    "For write operations, respond with a JSON action plan."
                ),
                backstory=backstory,
                tools=member.get("tools", []),
                llm=self._llm_string,
                verbose=False,
                memory=True,
                max_iter=self._max_iter,
            )
            agents.append(agent)

        # Create tasks — each agent gets the same user request but with their role context
        tasks = []
        for agent in agents:
            task = Task(
                description=(
                    f"{user_message}\n\n"
                    "IMPORTANT: If the request is ambiguous or missing critical details, "
                    "ask a clarifying question before acting.\n"
                    "If you need to modify external state, respond with a JSON action plan.\n"
                    '{"type": "action_plan", "summary": "...", "steps": [...]}\n\n'
                    "For read-only queries, contribute your analysis directly."
                ),
                expected_output="A clarifying question, your analysis, or a JSON action plan",
                agent=agent,
            )
            tasks.append(task)

        crew = Crew(
            agents=agents,
            tasks=tasks,
            process=Process.sequential,  # agents work one after another
            verbose=False,
            memory=True,
        )

        try:
            result = self._run_with_timeout(crew)
            output = str(result.raw) if hasattr(result, "raw") else str(result)
        except TimeoutError:
            logger.error("Team crew timed out after %ds", self._crew_timeout)
            return f"⚠️ Team request timed out after {self._crew_timeout}s."
        except Exception as e:
            logger.error("Team crew failed: %s", e)
            return f"⚠️ Team execution failed: {e}"

        parsed = _extract_json(output)
        if parsed and parsed.get("type") == "action_plan":
            return parsed

        return output

    def _run_crew(self, agent: Agent, user_message: str) -> dict | str:
        """Create a one-shot Crew and run it. Returns dict (action_plan) or str."""
        task = Task(
            description=(
                f"{user_message}\n\n"
                "IMPORTANT: Choose ONE of these response modes:\n\n"
                "1. CLARIFY — If the request is ambiguous, missing critical details, or could be "
                "interpreted multiple ways, ask a short clarifying question. Examples of ambiguity: "
                "no target database specified, unclear priority/deadline, vague scope like 'update the thing', "
                "or multiple possible actions. Do NOT guess — ask.\n\n"
                "2. ACTION PLAN — If you need to modify external state (create/update/delete anything) "
                "AND the request is clear enough to act on, respond with ONLY a JSON action plan:\n"
                '{"type": "action_plan", "summary": "...", "steps": [{"service": "...", '
                '"action": "...", "params": {...}, "description": "..."}], "needs_approval": true}\n\n'
                "Valid services: notion, jira, confluence, calendar, gmail, slack, web_search, code_exec\n"
                "Valid notion actions: create_task, update_task_status, create_project, create_note, "
                "create_journal_entry, log_expense, log_workout, create_database, create_entry, "
                "create_standalone_page, add_page_content\n\n"
                "3. DIRECT ANSWER — For read-only queries, answer directly using your tools and context."
            ),
            expected_output="A clarifying question, a helpful response, or a JSON action plan for approval",
            agent=agent,
        )

        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            verbose=False,
            memory=True,
        )

        try:
            result = self._run_with_timeout(crew)
            output = str(result.raw) if hasattr(result, "raw") else str(result)
        except TimeoutError:
            logger.error("Crew execution timed out after %ds", self._crew_timeout)
            return f"⚠️ Request timed out after {self._crew_timeout}s. Try a simpler request."
        except Exception as e:
            logger.error("Crew execution failed: %s", e)
            return f"⚠️ CrewAI execution failed: {e}"

        # Try to parse as action plan
        parsed = _extract_json(output)
        if parsed and parsed.get("type") == "action_plan":
            return parsed

        return output

    def _run_with_timeout(self, crew: Crew):
        """Run crew.kickoff() with a SIGALRM timeout (Unix only)."""
        def _timeout_handler(signum, frame):
            raise TimeoutError(f"Crew execution exceeded {self._crew_timeout}s")

        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(self._crew_timeout)
        try:
            return crew.kickoff()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

    @staticmethod
    def _format_history(history: list[dict] | None) -> str:
        if not history:
            return "(no prior messages in this thread)"
        lines = []
        for msg in history:
            role = "User" if msg["role"] == "user" else "Jibsa"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)
