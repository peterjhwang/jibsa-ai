"""
NotionSecondBrain — schema-free Notion operations.

Two entry points for the orchestrator:
  1. get_context_for_request(user_message)  → str  (enriches Claude prompt)
  2. execute_step(step)                     → dict (executes an approved plan step)

Reads: any database is flattened to plain key-value JSON — no field mapping needed.
Writes: property names are auto-discovered from the database schema at runtime.
All errors return {"ok": False, ...} — they never propagate to the orchestrator.
"""
import json
import logging
import os
import re
from datetime import date as date_type
from pathlib import Path
from typing import Any

import yaml

from .notion_client import NotionAPIError, NotionClient
from .notion_db_registry import DatabaseRegistry
from .notion_db_templates import DB_TEMPLATES

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_CACHE_PATH = _CONFIG_DIR / "notion_databases_cache.json"


# ---------------------------------------------------------------------------
# Page flattener — converts any Notion page to a clean key-value dict
# ---------------------------------------------------------------------------

def _flatten_page(page: dict) -> dict:
    """Extract all property values from a Notion page without knowing its schema."""
    result: dict[str, Any] = {"_id": page.get("id", ""), "_url": page.get("url", "")}
    for raw_name, prop in page.get("properties", {}).items():
        name = raw_name.strip()  # strip trailing spaces common in templates
        ptype = prop.get("type", "")
        value: Any = None
        if ptype == "title":
            value = "".join(t.get("plain_text", "") for t in prop.get("title", []))
        elif ptype == "rich_text":
            value = "".join(t.get("plain_text", "") for t in prop.get("rich_text", []))
        elif ptype in ("select", "status"):
            sel = prop.get(ptype) or {}
            value = sel.get("name")
        elif ptype == "multi_select":
            names = [s.get("name") for s in prop.get("multi_select", [])]
            value = names or None
        elif ptype == "date":
            d = prop.get("date") or {}
            value = d.get("start")
        elif ptype == "checkbox":
            value = prop.get("checkbox")
        elif ptype == "number":
            value = prop.get("number")
        elif ptype == "formula":
            f = prop.get("formula", {})
            value = f.get(f.get("type", ""))
        # skip: relation, rollup, created_time, last_edited_time, people, files
        if value is not None and value != "" and value != []:
            result[name] = value
    return result


# ---------------------------------------------------------------------------
# Block content helpers
# ---------------------------------------------------------------------------

def _paragraph_block(text: str) -> dict:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _heading2_block(text: str) -> dict:
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def _text_to_blocks(content: str | list) -> list[dict]:
    """Convert content to Notion blocks. Accepts plain text or a list of block dicts."""
    if isinstance(content, list):
        return content
    return [_paragraph_block(p) for p in content.split("\n") if p.strip()]


# ---------------------------------------------------------------------------
# Property builder helpers — used for writes only
# ---------------------------------------------------------------------------

def _title_prop(text: str) -> dict:
    return {"title": [{"text": {"content": text}}]}

def _select_prop(value: str) -> dict:
    return {"select": {"name": value}}

def _status_prop(value: str) -> dict:
    return {"status": {"name": value}}

def _date_prop(iso_date: str) -> dict:
    return {"date": {"start": iso_date}}

def _rich_text_prop(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text}}]}

def _multi_select_prop(values: list[str]) -> dict:
    return {"multi_select": [{"name": v} for v in values]}

def _get_url(page: dict) -> str:
    return page.get("url", "")


# ---------------------------------------------------------------------------
# Property type → builder mapping (for create_entry auto-mapping)
# ---------------------------------------------------------------------------

_PROP_BUILDERS = {
    "title": _title_prop,
    "rich_text": _rich_text_prop,
    "select": _select_prop,
    "status": _status_prop,
    "date": _date_prop,
    "multi_select": lambda v: _multi_select_prop(v if isinstance(v, list) else [v]),
    "number": lambda v: {"number": float(v)},
    "checkbox": lambda v: {"checkbox": bool(v)},
}


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class NotionSecondBrain:
    def __init__(
        self,
        client: NotionClient,
        db_registry: DatabaseRegistry,
        parent_page_id: str = "",
        user_registry: "Any | None" = None,
        user_id: str = "",
    ):
        self._client = client
        self._registry = db_registry
        self._parent_page_id = parent_page_id
        self._schema_cache: dict[str, dict] = {}
        # Per-user mode: if set, cache saves go to credential store
        self._user_registry = user_registry
        self._user_id = user_id

    # -----------------------------------------------------------------------
    # DB lookup helpers — delegate to registry
    # -----------------------------------------------------------------------

    def _get_db_id(self, name: str) -> str:
        return self._registry.get_db_id(name)

    def _get_matching_dbs(self, message: str) -> list[dict]:
        return self._registry.get_matching_dbs(message)

    # -----------------------------------------------------------------------
    # Lazy DB creation — _ensure_db()
    # -----------------------------------------------------------------------

    def _ensure_db(self, name: str) -> str:
        """Get DB ID by name, auto-creating from template if parent_page_id is set."""
        db_id = self._registry.get_db_id(name)
        if db_id:
            return db_id

        if not self._parent_page_id:
            return ""

        template = DB_TEMPLATES.get(name)
        if not template:
            return ""

        try:
            db = self._client.create_database(
                parent_page_id=self._parent_page_id,
                title=name,
                properties=template["properties"],
            )
            new_id = db["id"]
            keywords = template.get("keywords", [])
            self._registry.register(name, new_id, keywords)
            self._save_registry_cache()
            logger.info("Auto-created database '%s' → %s", name, new_id)
            return new_id
        except NotionAPIError as e:
            logger.error("Failed to auto-create database '%s': %s", name, e)
            return ""

    def _save_registry_cache(self) -> None:
        """Save registry to per-user credential store or file cache."""
        if self._user_registry and self._user_id:
            self._user_registry.save_registry(self._user_id, self._registry)
        else:
            self._registry.save_cache(_CACHE_PATH)

    # -----------------------------------------------------------------------
    # Schema auto-discovery helpers (for writes)
    # -----------------------------------------------------------------------

    def _get_schema(self, db_id: str) -> dict:
        """Fetch and cache property schema for a database."""
        if db_id not in self._schema_cache:
            try:
                ds = self._client.get_database_schema(db_id)
                self._schema_cache[db_id] = ds
            except Exception as e:
                logger.warning("Could not fetch schema for %s: %s", db_id, e)
                self._schema_cache[db_id] = {}
        return self._schema_cache[db_id]

    def _title_prop_name(self, schema: dict) -> str:
        for name, prop in schema.items():
            if prop.get("type") == "title":
                return name
        return "Name"

    def _prop_by_type(self, schema: dict, ptype: str) -> str | None:
        for name, prop in schema.items():
            if prop.get("type") == ptype:
                return name
        return None

    def _prop_by_keyword(self, schema: dict, *keywords: str) -> str | None:
        for name in schema:
            clean = name.strip().lower()
            if any(kw.lower() in clean for kw in keywords):
                return name
        return None

    # -----------------------------------------------------------------------
    # Entry point 1: Context enrichment
    # -----------------------------------------------------------------------

    def get_context_for_request(self, user_message: str) -> str:
        """
        Returns formatted Notion context to inject into the Claude prompt.
        Queries databases whose keywords match the message; falls back to
        Tasks + Projects. Returns "" on any error (fail-open).
        """
        matched = self._get_matching_dbs(user_message)
        if not matched:
            all_dbs = self._registry.all_databases()
            matched = [db for db in all_dbs if db["name"] in ("Tasks", "Projects")]

        sections = []
        for db in matched[:4]:  # cap at 4 DBs to keep prompt lean
            db_id = db.get("id", "")
            if not db_id:
                continue
            try:
                pages = self._client.query_database(db_id, page_size=10)
                if not pages:
                    continue
                items = [_flatten_page(p) for p in pages]
                sections.append(
                    f"**{db['name']}**\n```json\n{json.dumps(items, indent=2, default=str)}\n```"
                )
            except NotionAPIError as e:
                logger.warning("Could not query %s: %s", db["name"], e)

        return "\n\n".join(sections)

    # -----------------------------------------------------------------------
    # Entry point 2: Step execution
    # -----------------------------------------------------------------------

    def execute_step(self, step: dict) -> dict:
        """Dispatch a single action plan step. Always returns a dict — never raises."""
        action = step.get("action", "")
        params = step.get("params", {})
        description = step.get("description", action)

        handlers = {
            "create_task":          self._create_task,
            "update_task_status":   self._update_task_status,
            "create_project":       self._create_project,
            "create_note":          self._create_note,
            "create_journal_entry": self._create_journal_entry,
            "log_expense":          self._log_expense,
            "log_workout":          self._log_workout,
            "create_database":      self._create_database,
            "create_entry":         self._create_entry,
            "create_standalone_page": self._create_standalone_page,
            "add_page_content":     self._add_page_content,
        }

        handler = handlers.get(action)
        if not handler:
            return {"ok": False, "error": f"Unknown action: {action}", "description": description}

        try:
            result = handler(params)
            result["description"] = description
            return result
        except NotionAPIError as e:
            logger.error("Notion step %s failed: %s", action, e)
            return {"ok": False, "error": str(e), "description": description}
        except Exception as e:
            logger.error("Unexpected error in step %s: %s", action, e)
            return {"ok": False, "error": f"Unexpected error: {e}", "description": description}

    # -----------------------------------------------------------------------
    # Legacy action handlers — now use _ensure_db() for lazy creation
    # -----------------------------------------------------------------------

    def _create_task(self, params: dict) -> dict:
        db_id = self._ensure_db("Tasks")
        if not db_id:
            return {"ok": False, "error": "Tasks database not configured"}
        schema = self._get_schema(db_id)
        props: dict[str, Any] = {self._title_prop_name(schema): _title_prop(params["name"])}
        if sp := self._prop_by_type(schema, "status"):
            props[sp] = _status_prop(params.get("status", "To Do"))
        if pp := self._prop_by_keyword(schema, "priority"):
            if priority := params.get("priority"):
                props[pp] = _select_prop(priority)
        if dp := self._prop_by_keyword(schema, "due"):
            if due_date := params.get("due_date"):
                props[dp] = _date_prop(due_date)
        page = self._client.create_page(database_id=db_id, properties=props)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _update_task_status(self, params: dict) -> dict:
        page_id = params.get("page_id", "")
        if not page_id:
            return {"ok": False, "error": "page_id required"}
        db_id = self._ensure_db("Tasks")
        schema = self._get_schema(db_id) if db_id else {}
        sp = self._prop_by_type(schema, "status") or "Status"
        page = self._client.update_page(page_id=page_id, properties={sp: _status_prop(params["status"])})
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _create_project(self, params: dict) -> dict:
        db_id = self._ensure_db("Projects")
        if not db_id:
            return {"ok": False, "error": "Projects database not configured"}
        schema = self._get_schema(db_id)
        props: dict[str, Any] = {self._title_prop_name(schema): _title_prop(params["name"])}
        if sp := self._prop_by_type(schema, "status"):
            props[sp] = _status_prop(params.get("status", "In Progress"))
        if dp := self._prop_by_keyword(schema, "end", "deadline", "due"):
            if deadline := params.get("deadline"):
                props[dp] = _date_prop(deadline)
        page = self._client.create_page(database_id=db_id, properties=props)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _create_note(self, params: dict) -> dict:
        db_id = self._ensure_db("Notes")
        if not db_id:
            return {"ok": False, "error": "Notes database not configured"}
        schema = self._get_schema(db_id)
        props: dict[str, Any] = {self._title_prop_name(schema): _title_prop(params["name"])}
        if tp := self._prop_by_keyword(schema, "tag"):
            if tags := params.get("tags"):
                props[tp] = _multi_select_prop(tags)
        children = [_paragraph_block(params["body"])] if params.get("body") else None
        page = self._client.create_page(database_id=db_id, properties=props, children=children)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _create_journal_entry(self, params: dict) -> dict:
        db_id = self._ensure_db("Journal Entries")
        if not db_id:
            return {"ok": False, "error": "Journal Entries database not configured"}
        entry_date = params.get("date", date_type.today().isoformat())
        schema = self._get_schema(db_id)
        props: dict[str, Any] = {self._title_prop_name(schema): _title_prop(f"Journal — {entry_date}")}
        if dp := self._prop_by_keyword(schema, "date"):
            props[dp] = _date_prop(entry_date)
        children: list[dict] = []
        if body := params.get("body"):
            children.append(_paragraph_block(body))
        if params.get("template") == "weekly":
            children += [
                _heading2_block("Wins"), _paragraph_block(""),
                _heading2_block("Challenges"), _paragraph_block(""),
                _heading2_block("Next Week"), _paragraph_block(""),
            ]
        page = self._client.create_page(database_id=db_id, properties=props, children=children or None)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _log_expense(self, params: dict) -> dict:
        db_id = self._ensure_db("Expense Record")
        if not db_id:
            return {"ok": False, "error": "Expense Record database not configured"}
        schema = self._get_schema(db_id)
        props: dict[str, Any] = {self._title_prop_name(schema): _title_prop(params.get("name", "Expense"))}
        if ap := self._prop_by_keyword(schema, "amount"):
            if amount := params.get("amount"):
                props[ap] = {"number": float(amount)}
        if dp := self._prop_by_keyword(schema, "date"):
            props[dp] = _date_prop(params.get("date", date_type.today().isoformat()))
        if np := self._prop_by_keyword(schema, "note"):
            if note := params.get("note"):
                props[np] = _rich_text_prop(note)
        page = self._client.create_page(database_id=db_id, properties=props)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _log_workout(self, params: dict) -> dict:
        db_id = self._ensure_db("Workouts")
        if not db_id:
            return {"ok": False, "error": "Workouts database not configured"}
        schema = self._get_schema(db_id)
        props: dict[str, Any] = {self._title_prop_name(schema): _title_prop(params.get("name", "Workout"))}
        if dp := self._prop_by_keyword(schema, "date"):
            props[dp] = _date_prop(params.get("date", date_type.today().isoformat()))
        if durp := self._prop_by_keyword(schema, "duration", "min"):
            if duration := params.get("duration_min"):
                props[durp] = {"number": int(duration)}
        if np := self._prop_by_keyword(schema, "note"):
            if notes := params.get("notes"):
                props[np] = _rich_text_prop(notes)
        page = self._client.create_page(database_id=db_id, properties=props)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    # -----------------------------------------------------------------------
    # New action handlers — Phase E
    # -----------------------------------------------------------------------

    def _create_database(self, params: dict) -> dict:
        """Create a custom database under the parent page."""
        if not self._parent_page_id:
            return {"ok": False, "error": "No parent page configured — cannot create database"}
        name = params.get("name", "")
        if not name:
            return {"ok": False, "error": "Database name is required"}

        # Build properties from params or use minimal default
        prop_defs = params.get("properties", [])
        properties: dict[str, Any] = {}
        if prop_defs:
            for p in prop_defs:
                pname = p.get("name", "")
                ptype = p.get("type", "rich_text")
                if not pname:
                    continue
                if ptype == "title":
                    properties[pname] = {"title": {}}
                elif ptype == "select":
                    options = [{"name": o} for o in p.get("options", [])]
                    properties[pname] = {"select": {"options": options}}
                elif ptype == "multi_select":
                    options = [{"name": o} for o in p.get("options", [])]
                    properties[pname] = {"multi_select": {"options": options}}
                elif ptype == "date":
                    properties[pname] = {"date": {}}
                elif ptype == "number":
                    properties[pname] = {"number": {"format": "number"}}
                elif ptype == "checkbox":
                    properties[pname] = {"checkbox": {}}
                elif ptype == "email":
                    properties[pname] = {"email": {}}
                elif ptype == "url":
                    properties[pname] = {"url": {}}
                else:
                    properties[pname] = {"rich_text": {}}

        # Ensure there's a title property
        if not any("title" in v for v in properties.values()):
            properties["Name"] = {"title": {}}

        try:
            db = self._client.create_database(
                parent_page_id=self._parent_page_id,
                title=name,
                properties=properties,
            )
            keywords = params.get("keywords", [])
            self._registry.register(name, db["id"], keywords)
            self._save_registry_cache()
            return {"ok": True, "database_id": db["id"], "url": db.get("url", "")}
        except NotionAPIError as e:
            return {"ok": False, "error": str(e)}

    def _create_entry(self, params: dict) -> dict:
        """Create a page in any named database with auto-mapped properties."""
        db_name = params.get("database", "")
        if not db_name:
            return {"ok": False, "error": "database name is required"}

        db_id = self._ensure_db(db_name)
        if not db_id:
            return {"ok": False, "error": f"Database '{db_name}' not found and cannot be auto-created"}

        schema = self._get_schema(db_id)
        title_name = self._title_prop_name(schema)
        title_value = params.get("title", params.get("name", "Untitled"))

        props: dict[str, Any] = {title_name: _title_prop(title_value)}

        # Auto-map flat key-value properties to schema
        flat_props = params.get("properties", {})
        for key, value in flat_props.items():
            # Find matching schema property
            matched_prop = self._prop_by_keyword(schema, key.lower())
            if not matched_prop:
                continue
            schema_type = schema[matched_prop].get("type", "rich_text")
            builder = _PROP_BUILDERS.get(schema_type)
            if builder and schema_type != "title":  # title already set
                try:
                    props[matched_prop] = builder(value)
                except (ValueError, TypeError):
                    logger.warning("Could not build prop %s (%s) with value %r", matched_prop, schema_type, value)

        page = self._client.create_page(database_id=db_id, properties=props)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _create_standalone_page(self, params: dict) -> dict:
        """Create a page directly under the parent page."""
        if not self._parent_page_id:
            return {"ok": False, "error": "No parent page configured — cannot create page"}

        title = params.get("title", "Untitled")
        content = params.get("content", "")

        children = _text_to_blocks(content) if content else None

        try:
            page = self._client.create_page_under_page(
                parent_page_id=self._parent_page_id,
                title=title,
                children=children,
            )
            return {"ok": True, "page_id": page["id"], "url": _get_url(page)}
        except NotionAPIError as e:
            return {"ok": False, "error": str(e)}

    def _add_page_content(self, params: dict) -> dict:
        """Append blocks to an existing page."""
        page_id = params.get("page_id", "")
        if not page_id:
            return {"ok": False, "error": "page_id is required"}
        content = params.get("content", "")
        if not content:
            return {"ok": False, "error": "content is required"}

        blocks = _text_to_blocks(content)
        try:
            self._client.append_blocks(page_id, blocks)
            return {"ok": True, "page_id": page_id}
        except NotionAPIError as e:
            return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_db_id(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})", value, re.IGNORECASE)
    return match.group(1) if match else value


# ---------------------------------------------------------------------------
# Discovery — find child databases under a parent page
# ---------------------------------------------------------------------------

def _discover_child_databases(client: NotionClient, parent_page_id: str) -> list[dict]:
    """List child databases under a page. Returns [{name, id}]."""
    try:
        blocks = client.list_child_blocks(parent_page_id, block_type="child_database")
        results = []
        for block in blocks:
            db_title = ""
            for t in block.get("child_database", {}).get("title", ""):
                if isinstance(t, dict):
                    db_title += t.get("plain_text", "")
                elif isinstance(t, str):
                    db_title = t
            if not db_title:
                db_title = block.get("child_database", {}).get("title", "")
            if isinstance(db_title, str) and db_title:
                results.append({"name": db_title, "id": block["id"]})
        return results
    except NotionAPIError as e:
        logger.warning("Could not discover child databases: %s", e)
        return []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_second_brain(config: dict) -> "NotionSecondBrain | None":
    if not config.get("integrations", {}).get("notion", {}).get("enabled", False):
        return None
    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        logger.warning("Notion integration enabled but NOTION_TOKEN is not set")
        return None

    client = NotionClient(token)

    # Build registry
    registry = DatabaseRegistry()

    # 1. Load from YAML override (highest priority)
    db_config_path = _CONFIG_DIR / "notion_databases.yaml"
    try:
        with open(db_config_path) as f:
            raw = yaml.safe_load(f).get("notion", {})
        for entry in raw.get("databases", []):
            db_id = _extract_db_id(entry.get("id", ""))
            if not db_id:
                logger.warning("Notion DB %r has no valid ID — skipped", entry.get("name"))
                continue
            registry.register(entry["name"], db_id, entry.get("keywords", []))
            logger.info("Notion DB %-25s → %s (yaml)", entry["name"], db_id)
    except FileNotFoundError:
        logger.info("notion_databases.yaml not found — using dynamic mode")

    # 2. Resolve parent_page_id
    parent_page_id = os.environ.get("NOTION_PARENT_PAGE_ID", "")
    if not parent_page_id:
        notion_cfg = config.get("integrations", {}).get("notion", {})
        parent_page_id = notion_cfg.get("parent_page_id", "")

    # 3. Discover child databases under parent page
    if parent_page_id:
        discovered = _discover_child_databases(client, parent_page_id)
        for db in discovered:
            if not registry.get_db_id(db["name"]):
                # Try to find keywords from templates
                template = DB_TEMPLATES.get(db["name"], {})
                keywords = template.get("keywords", [])
                registry.register(db["name"], db["id"], keywords)
                logger.info("Notion DB %-25s → %s (discovered)", db["name"], db["id"])

    # 4. Load cache (fills in any gaps not covered by YAML or discovery)
    registry.load_cache(_CACHE_PATH)

    # 5. Save updated cache
    registry.save_cache(_CACHE_PATH)

    db_count = len(registry.all_databases())
    logger.info("Notion connected (%d DBs: yaml+discovered+cached)", db_count)
    return NotionSecondBrain(client=client, db_registry=registry, parent_page_id=parent_page_id)


def _discover_databases_via_search(client: NotionClient) -> list[dict]:
    """Discover databases accessible to a user's integration via Notion search API."""
    try:
        results = client.search_pages(
            query="",
            filter={"property": "object", "value": "database"},
            page_size=50,
        )
        databases = []
        for db in results:
            title_parts = db.get("title", [])
            name = "".join(t.get("plain_text", "") for t in title_parts if isinstance(t, dict))
            if name and db.get("id"):
                databases.append({"name": name, "id": db["id"]})
        return databases
    except Exception as e:
        logger.warning("Could not discover databases via search: %s", e)
        return []


def build_user_second_brain(
    user_id: str,
    notion_oauth,
    user_registry,
    config: dict,
) -> "NotionSecondBrain | None":
    """Build a per-user NotionSecondBrain using the user's OAuth token.

    Returns None if the user has no Notion token.
    """
    token = notion_oauth.get_token(user_id)
    if not token:
        return None

    client = NotionClient(token)

    # Try to load existing registry
    registry = user_registry.get_registry(user_id)
    if registry is None:
        # First time — discover databases
        registry = DatabaseRegistry()
        discovered = _discover_databases_via_search(client)
        for db in discovered:
            template = DB_TEMPLATES.get(db["name"], {})
            keywords = template.get("keywords", [])
            registry.register(db["name"], db["id"], keywords)
            logger.info("User %s Notion DB %-25s → %s (discovered)", user_id, db["name"], db["id"])
        user_registry.save_registry(user_id, registry)

    db_count = len(registry.all_databases())
    logger.info("Per-user Notion for %s (%d DBs)", user_id, db_count)
    return NotionSecondBrain(
        client=client,
        db_registry=registry,
        parent_page_id="",  # per-user mode doesn't use a global parent page
        user_registry=user_registry,
        user_id=user_id,
    )
