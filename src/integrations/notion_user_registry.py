"""
NotionUserRegistry — per-user Notion database registry stored in CredentialStore.

Each user who connects their own Notion workspace gets their own DatabaseRegistry,
serialized as JSON and stored as (user_id, "notion_registry") in the credential store.
"""
import json
import logging

from .notion_db_registry import DatabaseRegistry

logger = logging.getLogger(__name__)

_SERVICE_NAME = "notion_registry"


class NotionUserRegistry:
    """Per-user Notion database registry backed by CredentialStore."""

    def __init__(self, credential_store):
        self._store = credential_store

    def get_registry(self, user_id: str) -> DatabaseRegistry | None:
        """Load and return a user's DatabaseRegistry, or None if not stored."""
        data = self._store.get(user_id, _SERVICE_NAME)
        if not data:
            return None

        registry = DatabaseRegistry()
        for entry in data.get("databases", []):
            name = entry.get("name", "")
            db_id = entry.get("id", "")
            if name and db_id:
                registry.register(name, db_id, entry.get("keywords", []))
        return registry

    def get_parent_page_id(self, user_id: str) -> str:
        """Return the stored parent_page_id for *user_id*, or '' if not set."""
        data = self._store.get(user_id, _SERVICE_NAME)
        if not data:
            return ""
        return data.get("parent_page_id", "")

    def set_parent_page_id(self, user_id: str, parent_page_id: str) -> None:
        """Store (or update) the parent_page_id for *user_id*."""
        data = self._store.get(user_id, _SERVICE_NAME) or {}
        data["parent_page_id"] = parent_page_id
        self._store.set(user_id, _SERVICE_NAME, data)
        logger.debug("Set parent_page_id for user=%s to %s", user_id, parent_page_id)

    def save_registry(self, user_id: str, registry: DatabaseRegistry) -> None:
        """Serialize and store a user's DatabaseRegistry."""
        # Preserve existing fields (e.g. parent_page_id) when overwriting databases.
        existing = self._store.get(user_id, _SERVICE_NAME) or {}
        databases = registry.all_databases()
        existing["databases"] = databases
        self._store.set(user_id, _SERVICE_NAME, existing)
        logger.debug("Saved notion registry for user=%s (%d databases)", user_id, len(databases))

    def delete_registry(self, user_id: str) -> None:
        """Remove a user's stored registry."""
        self._store.delete(user_id, _SERVICE_NAME)
        logger.debug("Deleted notion registry for user=%s", user_id)
