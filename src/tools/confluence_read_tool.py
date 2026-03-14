"""
ConfluenceReadTool — CrewAI tool for read-only Confluence searches.

This tool lets agents search Confluence pages during reasoning.
Write operations are NOT exposed here — those go through the
propose-approve flow via action_plan JSON.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass  # Confluence client injected as plain object


_HTML_TAG_RE = re.compile(r"<[^>]+>")


class ConfluenceQueryInput(BaseModel):
    """Input schema for Confluence queries."""

    query: str = Field(
        ...,
        description=(
            "Search query for Confluence pages (e.g. 'deployment guide', "
            "'meeting notes Q1')"
        ),
    )


class ConfluenceReadTool(BaseTool):
    name: str = "Search Confluence"
    description: str = (
        "Search Confluence for pages and documentation. "
        "Returns page titles, snippets, and URLs. This is read-only — "
        "to create or update pages, propose an action plan instead."
    )
    args_schema: Type[BaseModel] = ConfluenceQueryInput
    confluence_client: object = None  # Set after init to avoid pydantic issues

    def _run(self, query: str) -> str:
        if self.confluence_client is None:
            return "Confluence is not connected."

        try:
            cql = f'text ~ "{query}" ORDER BY lastmodified DESC'
            results = self.confluence_client.search(cql, limit=10)
            return self._format_search_results(results)
        except Exception as e:
            return f"Confluence search failed: {e}"

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_search_results(results: list) -> str:
        """Format Confluence search results as readable lines."""
        if not results:
            return "No Confluence pages found."

        lines: list[str] = []
        for page in results:
            title = page.get("title", "Untitled")

            space = page.get("space", {})
            space_label = space.get("name") or space.get("key", "Unknown")

            links = page.get("_links", {})
            url = links.get("webui", "")

            excerpt_raw = page.get("excerpt", "")
            excerpt = _HTML_TAG_RE.sub("", excerpt_raw).strip()
            if len(excerpt) > 200:
                excerpt = excerpt[:200] + "..."

            parts = [f"Title: {title}", f"Space: {space_label}"]
            if url:
                parts.append(f"URL: {url}")
            if excerpt:
                parts.append(f"Excerpt: {excerpt}")

            lines.append(" | ".join(parts))

        return "\n".join(lines)

    @classmethod
    def create(cls, confluence_client: object) -> ConfluenceReadTool:
        """Factory method to create with proper Confluence client reference."""
        tool = cls()
        tool.confluence_client = confluence_client
        return tool
