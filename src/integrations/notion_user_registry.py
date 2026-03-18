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

    def save_registry(self, user_id: str, registry: DatabaseRegistry) -> None:
        """Serialize and store a user's DatabaseRegistry."""
        databases = registry.all_databases()
        self._store.set(user_id, _SERVICE_NAME, {"databases": databases})
        logger.debug("Saved notion registry for user=%s (%d databases)", user_id, len(databases))

    def delete_registry(self, user_id: str) -> None:
        """Remove a user's stored registry."""
        self._store.delete(user_id, _SERVICE_NAME)
        logger.debug("Deleted notion registry for user=%s", user_id)
