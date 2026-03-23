"""
SOPStore — SQLite backend for SOP (Standard Operating Procedure) storage.

Stores SOPs in a local SQLite database alongside intern data.
Follows the same threading/locking pattern as InternStore.
"""
import json
import logging
import sqlite3
import threading
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data/jibsa.db"


class SOPStore:
    """SQLite CRUD for SOPs."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._lock = threading.Lock()

        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS sops (
                    id               TEXT PRIMARY KEY,
                    intern_id        TEXT,
                    name             TEXT NOT NULL,
                    trigger_keywords TEXT NOT NULL DEFAULT '[]',
                    description      TEXT NOT NULL DEFAULT '',
                    steps            TEXT NOT NULL DEFAULT '[]',
                    expected_output  TEXT NOT NULL DEFAULT '',
                    tools_required   TEXT NOT NULL DEFAULT '[]',
                    approval_required INTEGER NOT NULL DEFAULT 1,
                    priority         INTEGER NOT NULL DEFAULT 0,
                    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(intern_id, name)
                )
            """)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sops_intern_id ON sops(intern_id)"
            )
            # SQLite treats NULL != NULL in UNIQUE, so add a partial index
            # to enforce uniqueness for shared SOPs (intern_id IS NULL)
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_sops_shared_name "
                "ON sops(name) WHERE intern_id IS NULL"
            )
            self._conn.commit()

    def create(self, data: dict) -> dict:
        """Insert a new SOP. Returns {"ok": True, "id": str} or {"ok": False, "error": str}."""
        name = data.get("name", "").strip()
        if not name:
            return {"ok": False, "error": "SOP name is required"}

        sop_id = data.get("id") or uuid.uuid4().hex
        intern_id = data.get("intern_id")  # None = shared SOP

        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO sops (id, intern_id, name, trigger_keywords, description,
                                      steps, expected_output, tools_required,
                                      approval_required, priority)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sop_id,
                        intern_id,
                        name,
                        json.dumps(data.get("trigger_keywords", [])),
                        data.get("description", ""),
                        json.dumps(data.get("steps", [])),
                        data.get("expected_output", ""),
                        json.dumps(data.get("tools_required", [])),
                        1 if data.get("approval_required", True) else 0,
                        data.get("priority", 0),
                    ),
                )
                self._conn.commit()
                logger.info("Created SOP '%s' (id=%s)", name, sop_id)
                return {"ok": True, "id": sop_id}
            except sqlite3.IntegrityError:
                scope = f"intern '{intern_id}'" if intern_id else "shared"
                return {"ok": False, "error": f"A SOP named '{name}' already exists for {scope}"}

    def get(self, sop_id: str) -> dict | None:
        """Get a SOP by id. Returns dict or None."""
        row = self._conn.execute(
            "SELECT * FROM sops WHERE id = ?", (sop_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def get_by_name(self, name: str, intern_id: str | None = None) -> dict | None:
        """Get a SOP by name + intern scope. Case-insensitive on name."""
        if intern_id:
            row = self._conn.execute(
                "SELECT * FROM sops WHERE LOWER(name) = LOWER(?) AND intern_id = ?",
                (name, intern_id),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM sops WHERE LOWER(name) = LOWER(?) AND intern_id IS NULL",
                (name,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def list_for_intern(self, intern_id: str) -> list[dict]:
        """Return SOPs for a specific intern plus shared SOPs."""
        rows = self._conn.execute(
            "SELECT * FROM sops WHERE intern_id = ? OR intern_id IS NULL "
            "ORDER BY priority DESC, name ASC",
            (intern_id,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_shared(self) -> list[dict]:
        """Return only shared SOPs (intern_id IS NULL)."""
        rows = self._conn.execute(
            "SELECT * FROM sops WHERE intern_id IS NULL ORDER BY priority DESC, name ASC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list_all(self) -> list[dict]:
        """Return all SOPs."""
        rows = self._conn.execute(
            "SELECT * FROM sops ORDER BY intern_id, priority DESC, name ASC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update(self, sop_id: str, updates: dict) -> dict:
        """Update SOP fields. Returns {"ok": True} or {"ok": False, "error": str}."""
        existing = self.get(sop_id)
        if not existing:
            return {"ok": False, "error": f"No SOP with id '{sop_id}'"}

        set_clauses = []
        values = []

        text_fields = {"name", "description", "expected_output"}
        json_fields = {"trigger_keywords", "steps", "tools_required"}

        for key in text_fields:
            if key in updates:
                set_clauses.append(f"{key} = ?")
                values.append(updates[key])

        for key in json_fields:
            if key in updates:
                set_clauses.append(f"{key} = ?")
                values.append(json.dumps(updates[key]))

        if "intern_id" in updates:
            set_clauses.append("intern_id = ?")
            values.append(updates["intern_id"])

        if "approval_required" in updates:
            set_clauses.append("approval_required = ?")
            values.append(1 if updates["approval_required"] else 0)

        if "priority" in updates:
            set_clauses.append("priority = ?")
            values.append(updates["priority"])

        if not set_clauses:
            return {"ok": False, "error": "No updateable fields provided"}

        set_clauses.append("updated_at = datetime('now')")
        values.append(sop_id)

        with self._lock:
            self._conn.execute(
                f"UPDATE sops SET {', '.join(set_clauses)} WHERE id = ?",
                values,
            )
            self._conn.commit()

        logger.info("Updated SOP '%s' fields: %s", sop_id, list(updates.keys()))
        return {"ok": True}

    def delete(self, sop_id: str) -> dict:
        """Permanently delete a SOP."""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM sops WHERE id = ?", (sop_id,))
            self._conn.commit()
        if cursor.rowcount > 0:
            return {"ok": True}
        return {"ok": False, "error": f"No SOP with id '{sop_id}'"}

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        for key in ("trigger_keywords", "steps", "tools_required"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
        d["approval_required"] = bool(d.get("approval_required", 1))
        return d
