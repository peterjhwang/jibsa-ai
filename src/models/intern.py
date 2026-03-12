"""InternJD — data model for an intern's Job Description."""
from dataclasses import dataclass, field


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

    def matches_name(self, query: str) -> bool:
        """Case-insensitive name match."""
        return self.name.lower() == query.lower()
