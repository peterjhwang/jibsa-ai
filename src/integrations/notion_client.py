"""
NotionClient — thin wrapper around the official notion-client SDK.

Knows nothing about PARA or Jibsa's domain. Translates SDK exceptions
into a single local NotionAPIError so callers are insulated from SDK internals.
"""
import logging
from typing import Any

from notion_client import Client
from notion_client.errors import APIResponseError

logger = logging.getLogger(__name__)


class NotionAPIError(Exception):
    """Wraps notion-client APIResponseError with operation context."""

    def __init__(self, operation: str, cause: Exception):
        super().__init__(f"Notion {operation} failed: {cause}")
        self.cause = cause


class NotionClient:
    def __init__(self, token: str):
        self._client = Client(auth=token)

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

    def get_page(self, page_id: str) -> dict:
        """Retrieve a single page by ID."""
        try:
            return self._client.pages.retrieve(page_id=page_id)
        except APIResponseError as e:
            raise NotionAPIError("get_page", e) from e
