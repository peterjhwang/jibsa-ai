"""
ConfluenceClient — thin wrapper around atlassian-python-api's Confluence class.

Knows nothing about Jibsa's domain. Translates SDK exceptions
into a single local ConfluenceAPIError so callers are insulated from SDK internals.

All public methods use tenacity retry with exponential backoff for transient
failures (rate limits, server errors). Non-retryable errors propagate immediately.
"""
import logging
from typing import Any

from atlassian import Confluence
from requests.exceptions import HTTPError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient Confluence API errors worth retrying."""
    if isinstance(exc, HTTPError):
        # 429 = rate limit, 500+ = server errors, 502/503/504 = gateway errors
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status in (429, 500, 502, 503, 504)
    return False


_confluence_retry = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


class ConfluenceAPIError(Exception):
    """Wraps Confluence API errors with operation context."""

    def __init__(self, operation: str, cause: Exception):
        super().__init__(f"Confluence {operation} failed: {cause}")
        self.cause = cause


class ConfluenceClient:
    def __init__(self, server: str, email: str, api_token: str):
        self._client = Confluence(
            url=server,
            username=email,
            password=api_token,
            cloud=True,
        )

    # -------------------------------------------------------------------
    # Read methods
    # -------------------------------------------------------------------

    @_confluence_retry
    def get_page(self, page_id: str, expand: str = "body.storage") -> dict:
        """Retrieve a single page by ID."""
        logger.debug("get_page → id=%s expand=%s", page_id, expand)
        try:
            page = self._client.get_page_by_id(page_id, expand=expand)
            logger.debug("get_page ← title=%s", page.get("title"))
            return page
        except HTTPError as e:
            logger.error("get_page FAILED id=%s: %s", page_id, e)
            raise ConfluenceAPIError("get_page", e) from e

    @_confluence_retry
    def search(self, cql: str, limit: int = 10) -> list[dict]:
        """Search Confluence using CQL and return results list."""
        logger.debug("search → cql=%s limit=%d", cql, limit)
        try:
            response = self._client.cql(cql, limit=limit)
            results = response.get("results", [])
            logger.debug("search ← %d results", len(results))
            return results
        except HTTPError as e:
            logger.error("search FAILED cql=%s: %s", cql, e)
            raise ConfluenceAPIError("search", e) from e

    @_confluence_retry
    def get_page_children(self, page_id: str) -> list[dict]:
        """Get child pages of a given page."""
        logger.debug("get_page_children → id=%s", page_id)
        try:
            children = self._client.get_page_child_by_type(page_id, type="page")
            logger.debug("get_page_children ← %d children", len(children))
            return children
        except HTTPError as e:
            logger.error("get_page_children FAILED id=%s: %s", page_id, e)
            raise ConfluenceAPIError("get_page_children", e) from e

    # -------------------------------------------------------------------
    # Write methods
    # -------------------------------------------------------------------

    @_confluence_retry
    def create_page(
        self,
        space_key: str,
        title: str,
        body: str,
        parent_id: str | None = None,
    ) -> dict:
        """Create a new page in a space."""
        logger.debug("create_page → space=%s title=%s parent=%s", space_key, title, parent_id)
        try:
            page = self._client.create_page(
                space=space_key,
                title=title,
                body=body,
                parent_id=parent_id,
            )
            logger.debug("create_page ← id=%s", page.get("id"))
            return page
        except HTTPError as e:
            logger.error("create_page FAILED space=%s title=%s: %s", space_key, title, e)
            raise ConfluenceAPIError("create_page", e) from e

    @_confluence_retry
    def update_page(self, page_id: str, title: str, body: str) -> dict:
        """Update an existing page's title and body."""
        logger.debug("update_page → id=%s title=%s", page_id, title)
        try:
            page = self._client.update_page(page_id, title=title, body=body)
            logger.debug("update_page ← ok")
            return page
        except HTTPError as e:
            logger.error("update_page FAILED id=%s: %s", page_id, e)
            raise ConfluenceAPIError("update_page", e) from e

    @_confluence_retry
    def add_comment(self, page_id: str, body: str) -> dict:
        """Add a comment to a page."""
        logger.debug("add_comment → page=%s", page_id)
        try:
            comment = self._client.add_comment(page_id, body)
            logger.debug("add_comment ← ok")
            return comment
        except HTTPError as e:
            logger.error("add_comment FAILED page=%s: %s", page_id, e)
            raise ConfluenceAPIError("add_comment", e) from e

    # -------------------------------------------------------------------
    # Action plan dispatch
    # -------------------------------------------------------------------

    def execute_step(self, step: dict) -> dict:
        """Dispatch a single action plan step. Always returns a dict — never raises."""
        action = step.get("action", "")
        params = step.get("params", {})
        description = step.get("description", action)

        handlers = {
            "create_page":  self._handle_create_page,
            "update_page":  self._handle_update_page,
            "add_comment":  self._handle_add_comment,
        }

        handler = handlers.get(action)
        if not handler:
            return {"ok": False, "error": f"Unknown action: {action}", "description": description}

        try:
            result = handler(params)
            result["description"] = description
            return result
        except ConfluenceAPIError as e:
            logger.error("Confluence step %s failed: %s", action, e)
            return {"ok": False, "error": str(e), "description": description}
        except Exception as e:
            logger.error("Unexpected error in step %s: %s", action, e)
            return {"ok": False, "error": f"Unexpected error: {e}", "description": description}

    # -------------------------------------------------------------------
    # Step handlers
    # -------------------------------------------------------------------

    def _extract_url(self, response: dict) -> str | None:
        """Extract page URL from Confluence API response."""
        links = response.get("_links", {})
        base = links.get("base", "")
        webui = links.get("webui", "")
        if base and webui:
            return base + webui
        return None

    def _handle_create_page(self, params: dict) -> dict:
        page = self.create_page(
            space_key=params["space_key"],
            title=params["title"],
            body=params["body"],
            parent_id=params.get("parent_id"),
        )
        url = self._extract_url(page)
        return {"ok": True, "url": url, "title": page.get("title", params["title"])}

    def _handle_update_page(self, params: dict) -> dict:
        page = self.update_page(
            page_id=params["page_id"],
            title=params["title"],
            body=params["body"],
        )
        url = self._extract_url(page)
        return {"ok": True, "url": url, "title": page.get("title", params["title"])}

    def _handle_add_comment(self, params: dict) -> dict:
        self.add_comment(
            page_id=params["page_id"],
            body=params["body"],
        )
        return {"ok": True}
