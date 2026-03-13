"""
WebReaderTool — CrewAI tool for fetching and reading web page content.

Uses ZenRows to handle JavaScript rendering, anti-bot protection, and proxies.
Returns cleaned text content for the agent to analyze.
"""
import logging
import os
import re
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from zenrows import ZenRowsClient
except ImportError:  # pragma: no cover
    ZenRowsClient = None  # type: ignore[misc,assignment]

MAX_CONTENT_CHARS = 8000


class WebReaderInput(BaseModel):
    """Input schema for web reader."""
    url: str = Field(..., description="The URL of the web page to read")


class WebReaderTool(BaseTool):
    name: str = "Read Web Page"
    description: str = (
        "Fetch and read the content of a web page. "
        "Useful for reading articles, blog posts, documentation, and competitor pages. "
        "Returns the text content of the page (HTML stripped). "
        "Use this after Web Search to read full pages from search results."
    )
    args_schema: Type[BaseModel] = WebReaderInput

    def _run(self, url: str) -> str:
        api_key = os.environ.get("ZENROWS_API_KEY", "")
        if not api_key:
            return "Web reader is not configured — ZENROWS_API_KEY is not set."

        # Basic URL validation
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        try:
            if ZenRowsClient is None:
                return "Web reader is not available — zenrows package is not installed."
            client = ZenRowsClient(api_key)
            response = client.get(url)

            if response.status_code != 200:
                return f"Failed to fetch {url} — HTTP {response.status_code}"

            html = response.text
            text = _html_to_text(html)

            if not text.strip():
                return f"Page at {url} returned no readable content."

            # Truncate to keep prompt size manageable
            if len(text) > MAX_CONTENT_CHARS:
                text = text[:MAX_CONTENT_CHARS] + f"\n\n... (truncated, {len(response.text)} chars total)"

            return f"Content from {url}:\n\n{text}"

        except Exception as e:
            logger.warning("WebReader failed for %s: %s", url, e)
            return f"Failed to read {url}: {e}"


def _html_to_text(html: str) -> str:
    """Strip HTML tags and clean up whitespace. Simple regex-based approach."""
    # Remove script and style blocks
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<footer[^>]*>.*?</footer>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<header[^>]*>.*?</header>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # Convert common elements to text markers
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</?p[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</?div[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</?li[^>]*>", "\n- ", html, flags=re.IGNORECASE)
    html = re.sub(r"<h[1-6][^>]*>", "\n## ", html, flags=re.IGNORECASE)
    html = re.sub(r"</h[1-6]>", "\n", html, flags=re.IGNORECASE)

    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", "", html)

    # Decode common HTML entities
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&nbsp;", " ")

    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)

    return text.strip()
