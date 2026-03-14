"""
InternRegistry — CRUD for AI interns, backed by SQLite.

Manages the lifecycle of interns: create, list, get, update, deactivate.
Intern JDs are stored in a local SQLite database (via InternStore).
"""
import logging
from typing import Any

from .integrations.intern_store import InternStore
from .models.intern import InternJD

logger = logging.getLogger(__name__)


class InternRegistry:
    def __init__(self, intern_store: InternStore):
        self._store = intern_store

    def list_interns(self, force_refresh: bool = False) -> list[InternJD]:
        """Return all active interns."""
        rows = self._store.list_active()
        return [self._row_to_jd(r) for r in rows]

    def get_intern(self, name: str) -> InternJD | None:
        """Case-insensitive lookup by name. Returns None if not found or inactive."""
        row = self._store.get(name)
        if not row or not row.get("active", True):
            return None
        return self._row_to_jd(row)

    def get_intern_names(self) -> list[str]:
        """Return list of active intern names (lowercase)."""
        return [i.name.lower() for i in self.list_interns()]

    def create_intern(self, jd: InternJD) -> dict:
        """Save a new intern JD. Returns {"ok": bool, ...}."""
        # Check for duplicate
        if self.get_intern(jd.name):
            return {"ok": False, "error": f"An intern named '{jd.name}' already exists"}

        result = self._store.create({
            "name": jd.name,
            "role": jd.role,
            "responsibilities": jd.responsibilities,
            "tone": jd.tone,
            "tools_allowed": jd.tools_allowed,
            "autonomy_rules": jd.autonomy_rules,
            "created_by": jd.created_by,
        })

        if result.get("ok"):
            logger.info("Created intern '%s'", jd.name)
        return result

    def update_intern(self, name: str, updates: dict) -> dict:
        """Update an existing intern's JD fields.

        Args:
            name: Intern name (case-insensitive).
            updates: Dict of field names to new values. Supported keys:
                     role, responsibilities, tone, tools_allowed, autonomy_rules.

        Returns:
            {"ok": bool, ...}
        """
        intern = self.get_intern(name)
        if not intern:
            return {"ok": False, "error": f"No intern named '{name}'"}

        return self._store.update(name, updates)

    def deactivate_intern(self, name: str) -> dict:
        """Set an intern to inactive."""
        intern = self.get_intern(name)
        if not intern:
            return {"ok": False, "error": f"No intern named '{name}'"}

        return self._store.deactivate(name)

    def save_memory(self, name: str, memory: list[str], channel_memory: dict[str, list[str]] | None = None) -> None:
        """Persist intern memory to SQLite."""
        updates: dict[str, Any] = {"memory": memory}
        if channel_memory is not None:
            updates["channel_memory"] = channel_memory
        self._store.update(name, updates)

    @staticmethod
    def _row_to_jd(row: dict) -> InternJD:
        """Convert a store row dict to an InternJD."""
        tools = row.get("tools_allowed", [])
        if isinstance(tools, str):
            tools = [t.strip() for t in tools.split(",") if t.strip()]

        return InternJD(
            name=row["name"],
            role=row.get("role", ""),
            responsibilities=row.get("responsibilities", []),
            tone=row.get("tone", ""),
            tools_allowed=tools,
            autonomy_rules=row.get("autonomy_rules", ""),
            created_by=row.get("created_by", ""),
            active=row.get("active", True),
            memory=row.get("memory", []),
            channel_memory=row.get("channel_memory", {}),
        )
