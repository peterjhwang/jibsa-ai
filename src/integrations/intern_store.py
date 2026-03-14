"""
InternStore — SQLite backend for intern JD storage.

Stores intern Job Descriptions in a local SQLite database, removing the
hard dependency on Notion for intern management. Follows the same
threading/locking pattern as CredentialStore.
"""
import json
import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data/jibsa.db"


class InternStore:
    """SQLite CRUD for intern JDs."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._lock = threading.Lock()

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS interns (
                    name            TEXT PRIMARY KEY,
                    role            TEXT NOT NULL DEFAULT '',
                    responsibilities TEXT NOT NULL DEFAULT '[]',
                    tone            TEXT NOT NULL DEFAULT '',
                    tools_allowed   TEXT NOT NULL DEFAULT '[]',
                    autonomy_rules  TEXT NOT NULL DEFAULT '',
                    created_by      TEXT NOT NULL DEFAULT '',
                    active          INTEGER NOT NULL DEFAULT 1,
                    memory          TEXT NOT NULL DEFAULT '[]',
                    channel_memory  TEXT NOT NULL DEFAULT '{}',
                    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            self._conn.commit()

    def create(self, data: dict) -> dict:
        """Insert a new intern. Returns {"ok": True} or {"ok": False, "error": str}."""
        name = data.get("name", "").strip()
        if not name:
            return {"ok": False, "error": "Intern name is required"}

        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO interns (name, role, responsibilities, tone, tools_allowed,
                                        autonomy_rules, created_by, active, memory, channel_memory)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, '[]', '{}')
                    """,
                    (
                        name,
                        data.get("role", ""),
                        json.dumps(data.get("responsibilities", [])),
                        data.get("tone", ""),
                        json.dumps(data.get("tools_allowed", [])),
                        data.get("autonomy_rules", ""),
                        data.get("created_by", ""),
                    ),
                )
                self._conn.commit()
                logger.info("Created intern '%s' in SQLite", name)
                return {"ok": True}
            except sqlite3.IntegrityError:
                return {"ok": False, "error": f"An intern named '{name}' already exists"}

    def get(self, name: str) -> dict | None:
        """Get an intern by name (case-insensitive). Returns dict or None."""
        row = self._conn.execute(
            "SELECT * FROM interns WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def list_active(self) -> list[dict]:
        """Return all active interns."""
        rows = self._conn.execute(
            "SELECT * FROM interns WHERE active = 1 ORDER BY name"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_all(self) -> list[dict]:
        """Return all interns (active and inactive)."""
        rows = self._conn.execute("SELECT * FROM interns ORDER BY name").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update(self, name: str, updates: dict) -> dict:
        """Update intern fields. Returns {"ok": True} or {"ok": False, "error": str}."""
        existing = self.get(name)
        if not existing:
            return {"ok": False, "error": f"No intern named '{name}'"}

        set_clauses = []
        values = []

        field_map = {
            "role": "role",
            "tone": "tone",
            "autonomy_rules": "autonomy_rules",
            "created_by": "created_by",
        }
        json_field_map = {
            "responsibilities": "responsibilities",
            "tools_allowed": "tools_allowed",
            "memory": "memory",
            "channel_memory": "channel_memory",
        }

        for key, col in field_map.items():
            if key in updates:
                set_clauses.append(f"{col} = ?")
                values.append(updates[key])

        for key, col in json_field_map.items():
            if key in updates:
                set_clauses.append(f"{col} = ?")
                values.append(json.dumps(updates[key]))

        if "active" in updates:
            set_clauses.append("active = ?")
            values.append(1 if updates["active"] else 0)

        if not set_clauses:
            return {"ok": False, "error": "No updateable fields provided"}

        set_clauses.append("updated_at = datetime('now')")
        values.append(name)

        with self._lock:
            self._conn.execute(
                f"UPDATE interns SET {', '.join(set_clauses)} WHERE LOWER(name) = LOWER(?)",
                values,
            )
            self._conn.commit()

        logger.info("Updated intern '%s' fields: %s", name, list(updates.keys()))
        return {"ok": True}

    def deactivate(self, name: str) -> dict:
        """Set active = 0 for an intern."""
        return self.update(name, {"active": False})

    def delete(self, name: str) -> dict:
        """Permanently delete an intern. Returns {"ok": True/False}."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM interns WHERE LOWER(name) = LOWER(?)", (name,)
            )
            self._conn.commit()
        if cursor.rowcount > 0:
            return {"ok": True}
        return {"ok": False, "error": f"No intern named '{name}'"}

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        # Deserialize JSON fields
        for key in ("responsibilities", "tools_allowed", "memory"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
        if "channel_memory" in d and isinstance(d["channel_memory"], str):
            try:
                d["channel_memory"] = json.loads(d["channel_memory"])
            except (json.JSONDecodeError, TypeError):
                d["channel_memory"] = {}
        d["active"] = bool(d.get("active", 1))
        return d
