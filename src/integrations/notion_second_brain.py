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

logger = logging.getLogger(__name__)


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
# Main class
# ---------------------------------------------------------------------------

class NotionSecondBrain:
    def __init__(self, client: NotionClient, databases: list[dict]):
        """databases: list of {name, id, keywords} dicts from notion_databases.yaml"""
        self._client = client
        self._databases = databases
        self._schema_cache: dict[str, dict] = {}

    # -----------------------------------------------------------------------
    # DB lookup helpers
    # -----------------------------------------------------------------------

    def _get_db_id(self, name: str) -> str:
        for db in self._databases:
            if db["name"].lower() == name.lower():
                return db.get("id", "")
        return ""

    def _get_matching_dbs(self, message: str) -> list[dict]:
        msg = message.lower()
        return [db for db in self._databases
                if any(kw.lower() in msg for kw in db.get("keywords", []))]

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
            matched = [db for db in self._databases if db["name"] in ("Tasks", "Projects")]

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
            "create_task":        self._create_task,
            "update_task_status": self._update_task_status,
            "create_project":     self._create_project,
            "create_note":        self._create_note,
            "create_journal_entry": self._create_journal_entry,
            "log_expense":        self._log_expense,
            "log_workout":        self._log_workout,
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
    # Action handlers — schema auto-discovery, no hardcoded field names
    # -----------------------------------------------------------------------

    def _create_task(self, params: dict) -> dict:
        db_id = self._get_db_id("Tasks")
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
        db_id = self._get_db_id("Tasks")
        schema = self._get_schema(db_id) if db_id else {}
        sp = self._prop_by_type(schema, "status") or "Status"
        page = self._client.update_page(page_id=page_id, properties={sp: _status_prop(params["status"])})
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _create_project(self, params: dict) -> dict:
        db_id = self._get_db_id("Projects")
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
        db_id = self._get_db_id("Notes")
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
        db_id = self._get_db_id("Journal Entries")
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
        db_id = self._get_db_id("Expense Record")
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
        db_id = self._get_db_id("Workouts")
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_db_id(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})", value, re.IGNORECASE)
    return match.group(1) if match else value


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

    db_config_path = Path(__file__).parent.parent.parent / "config" / "notion_databases.yaml"
    try:
        with open(db_config_path) as f:
            raw = yaml.safe_load(f).get("notion", {})
    except FileNotFoundError:
        logger.warning("notion_databases.yaml not found at %s", db_config_path)
        raw = {}

    databases = []
    for entry in raw.get("databases", []):
        db_id = _extract_db_id(entry.get("id", ""))
        if not db_id:
            logger.warning("Notion DB %r has no valid ID — skipped", entry.get("name"))
            continue
        databases.append({"name": entry["name"], "id": db_id, "keywords": entry.get("keywords", [])})
        logger.info("Notion DB %-25s → %s", entry["name"], db_id)

    logger.info("Notion Second Brain connected (%d DBs configured)", len(databases))
    return NotionSecondBrain(client=NotionClient(token), databases=databases)
