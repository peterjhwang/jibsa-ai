"""
WebSearchTool — CrewAI tool for web search via DuckDuckGo.

No API key required. Interns with web_search in their tools_allowed
can use this during reasoning.
"""
from typing import Type

from crewai.tools import BaseTool
from duckduckgo_search import DDGS
from pydantic import BaseModel, Field


class WebSearchInput(BaseModel):
    """Input schema for web search."""
    query: str = Field(..., description="The search query")
    max_results: int = Field(default=5, description="Maximum number of results to return")


class WebSearchTool(BaseTool):
    name: str = "Web Search"
    description: str = (
        "Search the web for current information using DuckDuckGo. "
        "Returns titles, URLs, and snippets for the top results."
    )
    args_schema: Type[BaseModel] = WebSearchInput

    def _run(self, query: str, max_results: int = 5) -> str:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))

            if not results:
                return f"No results found for: {query}"

            lines = []
            for i, r in enumerate(results, 1):
                title = r.get("title", "")
                snippet = r.get("body", "")
                url = r.get("href", "")
                lines.append(f"{i}. {title}\n   {snippet}\n   URL: {url}")

            return "\n\n".join(lines)

        except Exception as e:
            return f"Web search failed: {e}"
