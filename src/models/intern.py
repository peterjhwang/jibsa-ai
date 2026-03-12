"""InternJD — data model for an intern's Job Description."""
from dataclasses import dataclass, field


# Required fields for a valid JD
REQUIRED_JD_FIELDS = ("name", "role", "responsibilities")

# Valid tool names that can be assigned to interns
VALID_TOOL_NAMES = {"notion", "web_search", "code_exec", "slack", "calendar"}


@dataclass
class InternJD:
    """Represents an intern's Job Description stored in Notion."""

    name: str                               # "Alex"
    role: str                               # "Content Marketing Intern"
    responsibilities: list[str]             # ["Write LinkedIn posts", ...]
    tone: str                               # "Professional but creative..."
    tools_allowed: list[str]                # ["notion", "web_search"]
    autonomy_rules: str                     # "Always propose before publishing"
    created_by: str = ""                    # Slack user ID
    notion_page_id: str = ""                # Notion page ID where this JD is stored
    active: bool = True
    memory: list[str] = field(default_factory=list)  # Per-intern memory entries (global)
    channel_memory: dict[str, list[str]] = field(default_factory=dict)  # Channel-scoped memory

    def matches_name(self, query: str) -> bool:
        """Case-insensitive name match."""
        return self.name.lower() == query.lower()

    def validate(self) -> list[str]:
        """
        Validate JD completeness. Returns list of error messages.
        Empty list = valid.
        """
        errors = []
        if not self.name or not self.name.strip():
            errors.append("Name is required")
        if not self.role or not self.role.strip():
            errors.append("Role is required")
        if not self.responsibilities:
            errors.append("At least one responsibility is required")
        if self.name and len(self.name) > 30:
            errors.append("Name must be 30 characters or fewer")
        if self.name and not self.name.replace(" ", "").replace("-", "").isalnum():
            errors.append("Name must be alphanumeric (spaces and hyphens OK)")

        # Validate tool names
        invalid_tools = [t for t in self.tools_allowed if t.lower() not in VALID_TOOL_NAMES]
        if invalid_tools:
            errors.append(f"Unknown tools: {', '.join(invalid_tools)}. Valid: {', '.join(sorted(VALID_TOOL_NAMES))}")

        return errors

    def format_jd(self) -> str:
        """Format JD as a readable Slack message."""
        responsibilities = "\n".join(f"  - {r}" for r in self.responsibilities)
        tools = ", ".join(self.tools_allowed) or "none"
        return (
            f"*Name:* {self.name}\n"
            f"*Role:* {self.role}\n"
            f"*Responsibilities:*\n{responsibilities}\n"
            f"*Tone:* {self.tone or 'Not specified'}\n"
            f"*Tools:* {tools}\n"
            f"*Autonomy Rules:* {self.autonomy_rules or 'Always propose before acting'}\n"
            f"*Active:* {'Yes' if self.active else 'No'}"
        )

    def add_memory(self, entry: str, channel: str = "") -> None:
        """Add a memory entry. If channel is provided, memory is scoped to that channel."""
        if channel:
            if channel not in self.channel_memory:
                self.channel_memory[channel] = []
            self.channel_memory[channel].append(entry)
            if len(self.channel_memory[channel]) > 20:
                self.channel_memory[channel] = self.channel_memory[channel][-20:]
        else:
            self.memory.append(entry)
            if len(self.memory) > 20:
                self.memory = self.memory[-20:]

    def get_memory_context(self, channel: str = "") -> str:
        """Format memory for injection into prompts. Channel-specific + global."""
        entries = list(self.memory)  # always include global
        if channel and channel in self.channel_memory:
            entries.extend(self.channel_memory[channel])
        if not entries:
            return ""
        lines = "\n".join(f"- {m}" for m in entries)
        return f"## Your Memory (things you've learned about the user and past work)\n{lines}"
