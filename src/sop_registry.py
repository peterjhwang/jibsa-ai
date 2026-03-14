"""
SOPRegistry — CRUD and resolution for SOPs, backed by SOPStore.

Manages SOP lifecycle and resolves which SOP applies to a given
intern + message combination via keyword matching.
"""
import logging
import re
from typing import Any

import yaml

from .integrations.sop_store import SOPStore
from .models.sop import SOP

logger = logging.getLogger(__name__)


class SOPRegistry:
    def __init__(self, sop_store: SOPStore):
        self._store = sop_store

    def create_sop(self, sop: SOP) -> dict:
        """Validate and create a SOP. Returns {"ok": bool, ...}."""
        errors = sop.validate()
        if errors:
            return {"ok": False, "error": "; ".join(errors)}

        return self._store.create({
            "id": sop.id or "",
            "intern_id": sop.intern_id,
            "name": sop.name,
            "trigger_keywords": sop.trigger_keywords,
            "description": sop.description,
            "steps": sop.steps,
            "expected_output": sop.expected_output,
            "tools_required": sop.tools_required,
            "approval_required": sop.approval_required,
            "priority": sop.priority,
        })

    def get_sop(self, sop_id: str) -> SOP | None:
        """Get a SOP by id."""
        row = self._store.get(sop_id)
        if not row:
            return None
        return self._row_to_sop(row)

    def get_sop_by_name(self, name: str, intern_id: str | None = None) -> SOP | None:
        """Get a SOP by name + scope."""
        row = self._store.get_by_name(name, intern_id)
        if not row:
            return None
        return self._row_to_sop(row)

    def list_sops_for_intern(self, intern_name: str) -> list[SOP]:
        """Return SOPs for a specific intern + shared SOPs."""
        rows = self._store.list_for_intern(intern_name)
        return [self._row_to_sop(r) for r in rows]

    def list_all_sops(self) -> list[SOP]:
        """Return all SOPs."""
        rows = self._store.list_all()
        return [self._row_to_sop(r) for r in rows]

    def update_sop(self, sop_id: str, updates: dict) -> dict:
        """Update SOP fields."""
        return self._store.update(sop_id, updates)

    def delete_sop(self, sop_id: str) -> dict:
        """Delete a SOP."""
        return self._store.delete(sop_id)

    def resolve_sops(self, intern_name: str, message: str) -> SOP | None:
        """Resolve which SOP applies to a given intern + message.

        Returns the best-matching SOP or None (freeform fallback).
        """
        candidates = self._store.list_for_intern(intern_name)
        if not candidates:
            return None

        # Tokenize message
        message_tokens = set(re.findall(r"\w+", message.lower()))

        scored: list[tuple[int, int, str, dict]] = []
        for row in candidates:
            keywords = {k.lower() for k in row.get("trigger_keywords", [])}
            score = len(keywords & message_tokens)
            if score > 0:
                scored.append((row.get("priority", 0), score, row.get("name", ""), row))

        if not scored:
            return None

        # Sort by priority DESC, score DESC, name ASC (deterministic tiebreak)
        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
        best = scored[0][3]

        logger.info(
            "SOP resolved: '%s' (priority=%d, score=%d) for intern '%s'",
            best["name"], best.get("priority", 0), scored[0][1], intern_name,
        )
        return self._row_to_sop(best)

    @staticmethod
    def seed_from_yaml(store: SOPStore, path: str) -> int:
        """Load SOPs from a YAML file and insert into the store.

        Returns count of SOPs successfully seeded.
        """
        with open(path) as f:
            data = yaml.safe_load(f)

        sops = data.get("sops", [])
        count = 0
        for entry in sops:
            name = entry.get("name", "")
            intern_id = entry.get("intern_id")
            # Skip if already exists
            if store.get_by_name(name, intern_id):
                logger.debug("SOP '%s' already exists, skipping seed", name)
                continue

            result = store.create(entry)
            if result.get("ok"):
                count += 1
                logger.info("Seeded SOP '%s'", name)
            else:
                logger.warning("Failed to seed SOP '%s': %s", name, result.get("error"))

        return count

    @staticmethod
    def _row_to_sop(row: dict) -> SOP:
        """Convert a store row dict to an SOP dataclass."""
        return SOP(
            id=row.get("id", ""),
            intern_id=row.get("intern_id"),
            name=row.get("name", ""),
            trigger_keywords=row.get("trigger_keywords", []),
            description=row.get("description", ""),
            steps=row.get("steps", []),
            expected_output=row.get("expected_output", ""),
            tools_required=row.get("tools_required", []),
            approval_required=row.get("approval_required", True),
            priority=row.get("priority", 0),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
        )
