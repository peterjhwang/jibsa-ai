"""SOP — data model for a Standard Operating Procedure."""
import re
from dataclasses import dataclass, field

from .intern import VALID_TOOL_NAMES

VALID_SOP_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass
class SOP:
    """Represents a Standard Operating Procedure stored in SQLite."""

    id: str = ""
    intern_id: str | None = None        # intern name or None (shared)
    name: str = ""                       # kebab-case, e.g. "weekly-report"
    trigger_keywords: list[str] = field(default_factory=list)
    description: str = ""               # becomes CrewAI Task description
    steps: list[str] = field(default_factory=list)
    expected_output: str = ""           # becomes CrewAI Task expected_output
    tools_required: list[str] = field(default_factory=list)
    approval_required: bool = True
    priority: int = 0
    created_at: str = ""
    updated_at: str = ""

    def validate(self) -> list[str]:
        """Validate SOP completeness. Returns list of error messages."""
        errors = []
        if not self.name or not self.name.strip():
            errors.append("Name is required")
        elif not VALID_SOP_NAME_RE.match(self.name):
            errors.append("Name must be kebab-case (lowercase, hyphens only, e.g. 'weekly-report')")
        elif len(self.name) > 50:
            errors.append("Name must be 50 characters or fewer")

        if not self.description or not self.description.strip():
            errors.append("Description is required")

        if not self.steps:
            errors.append("At least one step is required")

        if not self.trigger_keywords:
            errors.append("At least one trigger keyword is required")

        if not self.expected_output or not self.expected_output.strip():
            errors.append("Expected output is required")

        invalid_tools = [t for t in self.tools_required if t.lower() not in VALID_TOOL_NAMES]
        if invalid_tools:
            errors.append(
                f"Unknown tools: {', '.join(invalid_tools)}. "
                f"Valid: {', '.join(sorted(VALID_TOOL_NAMES))}"
            )

        if not (0 <= self.priority <= 100):
            errors.append("Priority must be between 0 and 100")

        return errors

    def format_sop(self) -> str:
        """Format SOP as a readable Slack message."""
        steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(self.steps))
        keywords = ", ".join(self.trigger_keywords) or "none"
        tools = ", ".join(self.tools_required) or "none"
        scope = f"Intern: {self.intern_id}" if self.intern_id else "Shared (all interns)"
        return (
            f"*Name:* {self.name}\n"
            f"*Scope:* {scope}\n"
            f"*Trigger Keywords:* {keywords}\n"
            f"*Description:* {self.description}\n"
            f"*Steps:*\n{steps}\n"
            f"*Expected Output:* {self.expected_output}\n"
            f"*Tools:* {tools}\n"
            f"*Approval Required:* {'Yes' if self.approval_required else 'No'}\n"
            f"*Priority:* {self.priority}"
        )

    def build_task_description(self, user_message: str) -> str:
        """Build a CrewAI Task description from this SOP + user message."""
        numbered_steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(self.steps))

        approval_instruction = (
            'respond with ONLY a JSON action plan:\n'
            '{"type": "action_plan", "summary": "...", "steps": [{"service": "...", '
            '"action": "...", "params": {...}, "description": "..."}], "needs_approval": true}\n\n'
            "Valid services: notion, jira, confluence, calendar, gmail, drive, slack, web_search, code_exec"
            if self.approval_required
            else "Respond directly with the result."
        )

        return (
            f"## SOP: {self.name}\n"
            f"{self.description}\n\n"
            f"### Procedure (follow these steps in order):\n"
            f"{numbered_steps}\n\n"
            f"### User Request:\n"
            f"{user_message}\n\n"
            f"### Response Instructions:\n"
            f"Follow the procedure above to fulfill the user's request. "
            f"If any step is ambiguous or you need more information, ask a clarifying question.\n"
            f"For any operation that modifies external state, {approval_instruction}"
        )

    def build_expected_output(self) -> str:
        """Return expected output for CrewAI Task."""
        return self.expected_output or "A helpful response following the SOP procedure."
