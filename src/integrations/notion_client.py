"""
NotionClient — thin wrapper around the official notion-client SDK.

Knows nothing about Jibsa's domain. Translates SDK exceptions
into a single local NotionAPIError so callers are insulated from SDK internals.

All public methods use tenacity retry with exponential backoff for transient
failures (rate limits, server errors). Non-retryable errors propagate immediately.
"""
import logging
from typing import Any

from notion_client import Client
from notion_client.errors import APIResponseError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient Notion API errors worth retrying."""
    if isinstance(exc, APIResponseError):
        # 429 = rate limit, 500+ = server errors, 502/503/504 = gateway errors
        return exc.status in (429, 500, 502, 503, 504)
    return False


_notion_retry = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)


class NotionAPIError(Exception):
    """Wraps notion-client APIResponseError with operation context."""

    def __init__(self, operation: str, cause: Exception):
        super().__init__(f"Notion {operation} failed: {cause}")
        self.cause = cause


class NotionClient:
    def __init__(self, token: str):
        self._client = Client(auth=token)

    @_notion_retry
    def query_database(
        self,
        database_id: str,
        filter: dict | None = None,
        sorts: list[dict] | None = None,
        page_size: int = 20,
    ) -> list[dict]:
        """Query a database and return list of page objects."""
        # Notion API 2025-09-03 uses data_sources/{id}/query instead of databases/{id}/query
        kwargs: dict[str, Any] = {"page_size": min(page_size, 100)}
        if filter:
            kwargs["filter"] = filter
        if sorts:
            kwargs["sorts"] = sorts
        logger.debug("query_database → db=%s filter=%s", database_id, filter)
        try:
            response = self._client.data_sources.query(
                data_source_id=database_id,
                **kwargs,
            )
            results = response.get("results", [])
            logger.debug("query_database ← %d results", len(results))
            return results
        except APIResponseError as e:
            logger.error("query_database FAILED db=%s: %s", database_id, e)
            raise NotionAPIError("query_database", e) from e

    @_notion_retry
    def create_page(
        self,
        database_id: str,
        properties: dict,
        children: list[dict] | None = None,
    ) -> dict:
        """Create a new page in a database."""
        kwargs: dict[str, Any] = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }
        if children:
            kwargs["children"] = children
        logger.debug("create_page → db=%s props=%s", database_id, list(properties.keys()))
        try:
            page = self._client.pages.create(**kwargs)
            logger.debug("create_page ← id=%s url=%s", page.get("id"), page.get("url"))
            return page
        except APIResponseError as e:
            logger.error("create_page FAILED db=%s: %s", database_id, e)
            raise NotionAPIError("create_page", e) from e

    @_notion_retry
    def update_page(self, page_id: str, properties: dict) -> dict:
        """Update properties on an existing page."""
        logger.debug("update_page → id=%s props=%s", page_id, list(properties.keys()))
        try:
            page = self._client.pages.update(page_id=page_id, properties=properties)
            logger.debug("update_page ← ok")
            return page
        except APIResponseError as e:
            logger.error("update_page FAILED id=%s: %s", page_id, e)
            raise NotionAPIError("update_page", e) from e

    @_notion_retry
    def search_pages(
        self,
        query: str,
        filter: dict | None = None,
        page_size: int = 10,
    ) -> list[dict]:
        """Search all accessible pages/databases by title."""
        kwargs: dict[str, Any] = {"query": query, "page_size": min(page_size, 100)}
        if filter:
            kwargs["filter"] = filter
        try:
            response = self._client.search(**kwargs)
            results = response.get("results", [])
            logger.debug("search '%s' → %d results", query, len(results))
            return results
        except APIResponseError as e:
            raise NotionAPIError("search_pages", e) from e

    @_notion_retry
    def get_page(self, page_id: str) -> dict:
        """Retrieve a single page by ID."""
        try:
            return self._client.pages.retrieve(page_id=page_id)
        except APIResponseError as e:
            raise NotionAPIError("get_page", e) from e

    @_notion_retry
    def get_database_schema(self, database_id: str) -> dict:
        """Return the properties dict (name → type info) for a database."""
        try:
            ds = self._client.data_sources.retrieve(data_source_id=database_id)
            return ds.get("properties", {})
        except APIResponseError as e:
            raise NotionAPIError("get_database_schema", e) from e

    @_notion_retry
    def create_database(self, parent_page_id: str, title: str, properties: dict) -> dict:
        """Create a database under a page. Returns the database object."""
        logger.debug("create_database → parent=%s title=%s", parent_page_id, title)
        try:
            db = self._client.databases.create(
                parent={"type": "page_id", "page_id": parent_page_id},
                title=[{"type": "text", "text": {"content": title}}],
                properties=properties,
            )
            logger.debug("create_database ← id=%s", db.get("id"))
            return db
        except APIResponseError as e:
            logger.error("create_database FAILED: %s", e)
            raise NotionAPIError("create_database", e) from e

    @_notion_retry
    def create_page_under_page(
        self,
        parent_page_id: str,
        title: str,
        children: list[dict] | None = None,
    ) -> dict:
        """Create a page under another page (not a database)."""
        kwargs: dict[str, Any] = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "properties": {
                "title": [{"type": "text", "text": {"content": title}}],
            },
        }
        if children:
            kwargs["children"] = children
        logger.debug("create_page_under_page → parent=%s title=%s", parent_page_id, title)
        try:
            page = self._client.pages.create(**kwargs)
            logger.debug("create_page_under_page ← id=%s", page.get("id"))
            return page
        except APIResponseError as e:
            logger.error("create_page_under_page FAILED: %s", e)
            raise NotionAPIError("create_page_under_page", e) from e

    @_notion_retry
    def list_child_blocks(self, block_id: str, block_type: str | None = None) -> list[dict]:
        """List child blocks of a block/page, optionally filtered by type. Handles pagination."""
        logger.debug("list_child_blocks → block=%s type=%s", block_id, block_type)
        try:
            results: list[dict] = []
            cursor = None
            while True:
                kwargs: dict[str, Any] = {"block_id": block_id, "page_size": 100}
                if cursor:
                    kwargs["start_cursor"] = cursor
                response = self._client.blocks.children.list(**kwargs)
                for block in response.get("results", []):
                    if block_type is None or block.get("type") == block_type:
                        results.append(block)
                if not response.get("has_more"):
                    break
                cursor = response.get("next_cursor")
            logger.debug("list_child_blocks ← %d blocks", len(results))
            return results
        except APIResponseError as e:
            logger.error("list_child_blocks FAILED: %s", e)
            raise NotionAPIError("list_child_blocks", e) from e

    @_notion_retry
    def append_blocks(self, block_id: str, children: list[dict]) -> dict:
        """Append content blocks to a page or block."""
        logger.debug("append_blocks → block=%s (%d children)", block_id, len(children))
        try:
            result = self._client.blocks.children.append(block_id=block_id, children=children)
            logger.debug("append_blocks ← ok")
            return result
        except APIResponseError as e:
            logger.error("append_blocks FAILED: %s", e)
            raise NotionAPIError("append_blocks", e) from e
