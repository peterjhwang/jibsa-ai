"""
DatabaseRegistry — tracks known Notion databases.

Sources: YAML config (override), parent page discovery, runtime creation.
Replaces the static list[dict] that was passed to NotionSecondBrain.
"""
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class DatabaseRegistry:
    """In-memory registry of known Notion databases with optional disk cache."""

    def __init__(self):
        self._databases: dict[str, dict] = {}  # name_lower → {name, id, keywords}

    def register(self, name: str, db_id: str, keywords: list[str] | None = None) -> None:
        """Register a database. Later registrations for the same name overwrite."""
        key = name.lower()
        self._databases[key] = {
            "name": name,
            "id": db_id,
            "keywords": keywords or [],
        }

    def get_db_id(self, name: str) -> str:
        """Case-insensitive lookup. Returns "" if not found."""
        entry = self._databases.get(name.lower())
        return entry["id"] if entry else ""

    def get_matching_dbs(self, message: str) -> list[dict]:
        """Return databases whose keywords appear in the message."""
        msg = message.lower()
        return [
            db for db in self._databases.values()
            if any(kw.lower() in msg for kw in db.get("keywords", []))
        ]

    def all_databases(self) -> list[dict]:
        """Return all registered databases as a list of dicts."""
        return list(self._databases.values())

    def save_cache(self, path: Path) -> None:
        """Persist registry to a JSON file."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(self.all_databases(), f, indent=2)
            logger.debug("Saved DB registry cache to %s (%d entries)", path, len(self._databases))
        except OSError as e:
            logger.warning("Could not save DB registry cache: %s", e)

    def load_cache(self, path: Path) -> None:
        """Restore registry from a JSON cache file. Does not overwrite existing entries."""
        if not path.exists():
            return
        try:
            with open(path) as f:
                entries = json.load(f)
            for entry in entries:
                name = entry.get("name", "")
                db_id = entry.get("id", "")
                if name and db_id and name.lower() not in self._databases:
                    self.register(name, db_id, entry.get("keywords", []))
            logger.debug("Loaded %d entries from DB registry cache", len(entries))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not load DB registry cache: %s", e)

    @classmethod
    def from_yaml(cls, databases: list[dict]) -> "DatabaseRegistry":
        """Build a registry from the legacy list[dict] format (notion_databases.yaml)."""
        registry = cls()
        for db in databases:
            name = db.get("name", "")
            db_id = db.get("id", "")
            if name and db_id:
                registry.register(name, db_id, db.get("keywords", []))
        return registry
