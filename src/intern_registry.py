"""
InternRegistry — CRUD for AI interns, backed by Notion.

Manages the lifecycle of interns: create, list, get, deactivate.
The Interns database in Notion stores all JDs.
"""
import logging
from typing import Any

from .integrations.notion_client import NotionAPIError, NotionClient
from .integrations.notion_second_brain import (
    NotionSecondBrain,
    _flatten_page,
    _title_prop,
    _rich_text_prop,
    _multi_select_prop,
)
from .models.intern import InternJD

logger = logging.getLogger(__name__)


class InternRegistry:
    def __init__(self, notion_brain: NotionSecondBrain | None, config: dict):
        self._brain = notion_brain
        self._config = config
        self._cache: list[InternJD] | None = None

    @property
    def _client(self) -> NotionClient | None:
        return self._brain._client if self._brain else None

    @property
    def _interns_db_id(self) -> str:
        if not self._brain:
            return ""
        return self._brain._ensure_db("Interns")

    def list_interns(self, force_refresh: bool = False) -> list[InternJD]:
        """Return all active interns. Cached unless force_refresh."""
        if self._cache is not None and not force_refresh:
            return [i for i in self._cache if i.active]

        self._cache = self._load_from_notion()
        return [i for i in self._cache if i.active]

    def get_intern(self, name: str) -> InternJD | None:
        """Case-insensitive lookup by name."""
        for intern in self.list_interns():
            if intern.matches_name(name):
                return intern
        return None

    def get_intern_names(self) -> list[str]:
        """Return list of active intern names (lowercase)."""
        return [i.name.lower() for i in self.list_interns()]

    def create_intern(self, jd: InternJD) -> dict:
        """Save a new intern JD to Notion. Returns {"ok": bool, ...}."""
        db_id = self._interns_db_id
        if not db_id or not self._client:
            return {"ok": False, "error": "Interns database not configured in Notion"}

        # Check for duplicate name
        if self.get_intern(jd.name):
            return {"ok": False, "error": f"An intern named '{jd.name}' already exists"}

        try:
            schema = self._brain._get_schema(db_id)
            title_name = self._brain._title_prop_name(schema)

            props: dict[str, Any] = {
                title_name: _title_prop(jd.name),
            }

            # Map fields to schema properties
            if role_prop := self._brain._prop_by_keyword(schema, "role"):
                props[role_prop] = _rich_text_prop(jd.role)
            if resp_prop := self._brain._prop_by_keyword(schema, "responsib"):
                props[resp_prop] = _rich_text_prop("\n".join(jd.responsibilities))
            if tone_prop := self._brain._prop_by_keyword(schema, "tone"):
                props[tone_prop] = _rich_text_prop(jd.tone)
            if tools_prop := self._brain._prop_by_keyword(schema, "tool"):
                props[tools_prop] = _multi_select_prop(jd.tools_allowed)
            if auto_prop := self._brain._prop_by_keyword(schema, "autonomy", "rule"):
                props[auto_prop] = _rich_text_prop(jd.autonomy_rules)
            if created_prop := self._brain._prop_by_keyword(schema, "created"):
                props[created_prop] = _rich_text_prop(jd.created_by)

            # Check for Active checkbox
            if active_prop := self._brain._prop_by_keyword(schema, "active"):
                props[active_prop] = {"checkbox": True}

            page = self._client.create_page(database_id=db_id, properties=props)
            jd.notion_page_id = page["id"]

            # Invalidate cache
            self._cache = None

            logger.info("Created intern '%s' (page %s)", jd.name, page["id"])
            return {"ok": True, "page_id": page["id"], "url": page.get("url", "")}

        except NotionAPIError as e:
            logger.error("Failed to create intern '%s': %s", jd.name, e)
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.error("Unexpected error creating intern '%s': %s", jd.name, e)
            return {"ok": False, "error": f"Unexpected error: {e}"}

    def update_intern(self, name: str, updates: dict) -> dict:
        """Update an existing intern's JD fields in Notion.

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
        if not intern.notion_page_id or not self._client:
            return {"ok": False, "error": "Cannot update intern — no Notion page ID"}

        try:
            db_id = self._interns_db_id
            schema = self._brain._get_schema(db_id) if db_id else {}

            props: dict[str, Any] = {}

            if "role" in updates:
                if prop := self._brain._prop_by_keyword(schema, "role"):
                    props[prop] = _rich_text_prop(updates["role"])
            if "responsibilities" in updates:
                if prop := self._brain._prop_by_keyword(schema, "responsib"):
                    resp_text = "\n".join(updates["responsibilities"])
                    props[prop] = _rich_text_prop(resp_text)
            if "tone" in updates:
                if prop := self._brain._prop_by_keyword(schema, "tone"):
                    props[prop] = _rich_text_prop(updates["tone"])
            if "tools_allowed" in updates:
                if prop := self._brain._prop_by_keyword(schema, "tool"):
                    props[prop] = _multi_select_prop(updates["tools_allowed"])
            if "autonomy_rules" in updates:
                if prop := self._brain._prop_by_keyword(schema, "autonomy", "rule"):
                    props[prop] = _rich_text_prop(updates["autonomy_rules"])

            if not props:
                return {"ok": False, "error": "No updateable fields provided"}

            self._client.update_page(
                page_id=intern.notion_page_id,
                properties=props,
            )
            self._cache = None
            logger.info("Updated intern '%s' fields: %s", name, list(updates.keys()))
            return {"ok": True}

        except NotionAPIError as e:
            logger.error("Failed to update intern '%s': %s", name, e)
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.error("Unexpected error updating intern '%s': %s", name, e)
            return {"ok": False, "error": f"Unexpected error: {e}"}

    def deactivate_intern(self, name: str) -> dict:
        """Set an intern's Active flag to false."""
        intern = self.get_intern(name)
        if not intern:
            return {"ok": False, "error": f"No intern named '{name}'"}
        if not intern.notion_page_id or not self._client:
            return {"ok": False, "error": "Cannot update intern — no Notion page ID"}

        try:
            db_id = self._interns_db_id
            schema = self._brain._get_schema(db_id) if db_id else {}
            active_prop = self._brain._prop_by_keyword(schema, "active") if schema else "Active"
            if not active_prop:
                active_prop = "Active"
            self._client.update_page(
                page_id=intern.notion_page_id,
                properties={active_prop: {"checkbox": False}},
            )
            self._cache = None
            logger.info("Deactivated intern '%s'", name)
            return {"ok": True}
        except Exception as e:
            logger.error("Failed to deactivate intern '%s': %s", name, e)
            return {"ok": False, "error": str(e)}

    def _load_from_notion(self) -> list[InternJD]:
        """Query the Interns database and return InternJD objects."""
        db_id = self._interns_db_id
        if not db_id or not self._client:
            logger.debug("No Interns database configured — returning empty list")
            return []

        try:
            pages = self._client.query_database(db_id, page_size=50)
        except NotionAPIError as e:
            logger.warning("Could not query Interns DB: %s", e)
            return []

        interns = []
        for page in pages:
            flat = _flatten_page(page)
            # Find the name (title field)
            name = ""
            for key in ("Name", "name", "Title", "title"):
                if key in flat:
                    name = flat[key]
                    break
            if not name:
                continue

            interns.append(InternJD(
                name=name,
                role=flat.get("Role", flat.get("role", "")),
                responsibilities=[
                    r.strip()
                    for r in flat.get("Responsibilities", flat.get("responsibilities", "")).split("\n")
                    if r.strip()
                ],
                tone=flat.get("Tone", flat.get("tone", "")),
                tools_allowed=flat.get("Tools Allowed", flat.get("tools_allowed", flat.get("Tools", []))),
                autonomy_rules=flat.get("Autonomy Rules", flat.get("autonomy_rules", flat.get("Autonomy", ""))),
                created_by=flat.get("Created By", flat.get("created_by", "")),
                notion_page_id=flat.get("_id", ""),
                active=flat.get("Active", flat.get("active", True)),
            ))

        logger.info("Loaded %d interns from Notion", len(interns))
        return interns
