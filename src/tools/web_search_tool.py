"""
WebSearchTool — CrewAI tool for web search.

Primary: DuckDuckGo (no API key). Fallback: ZenRows SERP API (if configured).
Includes a simple rate limiter to prevent IP bans from aggressive agents.
"""
import logging
import os
import time
import threading
from typing import Type

from crewai.tools import BaseTool
from duckduckgo_search import DDGS
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class _RateLimiter:
    """Simple token-bucket rate limiter for web search calls."""

    def __init__(self, max_calls: int = 10, window_seconds: float = 60.0):
        self._max_calls = max_calls
        self._window = window_seconds
        self._calls: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        """Return True if the call is allowed, False if rate limited."""
        now = time.monotonic()
        with self._lock:
            # Prune old entries
            self._calls = [t for t in self._calls if now - t < self._window]
            if len(self._calls) >= self._max_calls:
                return False
            self._calls.append(now)
            return True


# Shared rate limiter: 10 searches per minute
_search_limiter = _RateLimiter(max_calls=10, window_seconds=60.0)


class WebSearchInput(BaseModel):
    """Input schema for web search."""
    query: str = Field(..., description="The search query")
    max_results: int = Field(default=5, description="Maximum number of results to return")


class WebSearchTool(BaseTool):
    name: str = "Web Search"
    description: str = (
        "Search the web for current information. "
        "Returns titles, URLs, and snippets for the top results."
    )
    args_schema: Type[BaseModel] = WebSearchInput

    def _run(self, query: str, max_results: int = 5) -> str:
        if not _search_limiter.acquire():
            return "Rate limited: too many searches in a short time. Wait a moment and try again."

        # Try DuckDuckGo first
        results = self._search_ddg(query, max_results)

        # Fallback to ZenRows SERP if DDG fails and key is available
        if results is None:
            zenrows_key = os.environ.get("ZENROWS_API_KEY", "")
            if zenrows_key:
                logger.info("DuckDuckGo failed, falling back to ZenRows SERP")
                results = self._search_zenrows(query, max_results, zenrows_key)

        if results is None:
            return f"Web search failed for: {query}. Try again later."

        if not results:
            return f"No results found for: {query}"

        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            snippet = r.get("body", r.get("snippet", ""))
            url = r.get("href", r.get("url", ""))
            lines.append(f"{i}. {title}\n   {snippet}\n   URL: {url}")

        return "\n\n".join(lines)

    @staticmethod
    def _search_ddg(query: str, max_results: int) -> list[dict] | None:
        """Search via DuckDuckGo. Returns results list or None on failure."""
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))
        except Exception as e:
            logger.warning("DuckDuckGo search failed: %s", e)
            return None

    @staticmethod
    def _search_zenrows(query: str, max_results: int, api_key: str) -> list[dict] | None:
        """Search via ZenRows SERP API. Returns results list or None on failure."""
        try:
            from zenrows import ZenRowsClient
            client = ZenRowsClient(api_key)
            response = client.get(
                f"https://www.google.com/search?q={query}&num={max_results}",
                params={"js_render": "true"},
            )
            if response.status_code != 200:
                logger.warning("ZenRows SERP returned HTTP %d", response.status_code)
                return None
            # ZenRows returns HTML; extract basic result patterns
            import re
            results = []
            # Simple extraction of search results from rendered HTML
            for match in re.finditer(r'<h3[^>]*>(.*?)</h3>', response.text):
                title = re.sub(r'<[^>]+>', '', match.group(1))
                if title.strip():
                    results.append({"title": title.strip(), "body": "", "href": ""})
                if len(results) >= max_results:
                    break
            return results if results else None
        except Exception as e:
            logger.warning("ZenRows SERP search failed: %s", e)
            return None
