"""
NotionSecondBrain — PARA-aware Notion operations.

Two entry points for the orchestrator:
  1. get_context_for_request(user_message)  → str  (enriches Claude prompt)
  2. execute_step(step)                     → dict (executes an approved plan step)

All write operations validate that the target DB ID is configured before calling the API.
All errors return {"ok": False, ...} — they never propagate to the orchestrator.
"""
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
# Property builder helpers (pure functions — no I/O)
# ---------------------------------------------------------------------------


def _title_prop(text: str) -> dict:
    return {"title": [{"text": {"content": text}}]}


def _select_prop(value: str) -> dict:
    return {"select": {"name": value}}


def _status_prop(value: str) -> dict:
    return {"status": {"name": value}}


def _date_prop(iso_date: str) -> dict:
    return {"date": {"start": iso_date}}


def _relation_prop(page_id: str) -> dict:
    return {"relation": [{"id": page_id}]}


def _rich_text_prop(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text}}]}


def _multi_select_prop(values: list[str]) -> dict:
    return {"multi_select": [{"name": v} for v in values]}


# ---------------------------------------------------------------------------
# Block content helpers
# ---------------------------------------------------------------------------


def _paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _heading2_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


# ---------------------------------------------------------------------------
# Page property parser helpers
# ---------------------------------------------------------------------------


def _get_title(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            parts = prop.get("title", [])
            return "".join(t.get("plain_text", "") for t in parts)
    return ""


def _get_select(page: dict, prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name, {})
    sel = prop.get("select")
    return sel.get("name", "") if sel else ""


def _get_date_start(page: dict, prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name, {})
    d = prop.get("date")
    return d.get("start", "") if d else ""


def _get_url(page: dict) -> str:
    return page.get("url", "")


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


# Default property names — overridden by config/notion_databases.yaml `properties:` section
_DEFAULT_PROPS = {
    "tasks":         {"name": "Name", "status": "Status", "priority": "Priority", "due_date": "Due Date", "project": "Project"},
    "projects":      {"name": "Name", "status": "Status", "owner": "Owner", "deadline": "Deadline"},
    "meeting_notes": {"name": "Name", "date": "Date", "attendees": "Attendees", "project": "Project"},
    "journal":       {"name": "Name", "date": "Date"},
    "knowledge_base":{"name": "Name", "tags": "Tags"},
    "crm":           {"name": "Name", "company": "Company", "role": "Role", "last_contacted": "Last Contacted", "notes": "Notes"},
}


class NotionSecondBrain:
    def __init__(self, client: NotionClient, db_ids: dict, props: dict | None = None):
        self._client = client
        self._db = db_ids
        # Merge user-supplied property names over defaults
        self._props: dict[str, dict] = {}
        user_props = props or {}
        for db, defaults in _DEFAULT_PROPS.items():
            self._props[db] = {**defaults, **(user_props.get(db, {}))}

    def _p(self, db: str, field: str) -> str:
        """Return the configured Notion property name for a given db + field."""
        return self._props.get(db, {}).get(field, field)

    # -----------------------------------------------------------------------
    # Entry point 1: Context enrichment
    # -----------------------------------------------------------------------

    def get_context_for_request(self, user_message: str) -> str:
        """
        Returns a formatted Notion context string to inject into the Claude prompt.
        Uses keyword routing to fetch only relevant data.
        Returns "" on any error (fail-open — a Notion outage must not block the bot).
        """
        msg = user_message.lower()
        sections: list[str] = []

        try:
            wants_tasks = any(kw in msg for kw in ("task", "todo", "reminder", "due", "overdue"))
            wants_projects = any(kw in msg for kw in ("project",))
            wants_meeting = any(kw in msg for kw in ("meeting", "prep", "agenda", "sync", "call"))

            if wants_meeting:
                sections.append(self._meeting_context())
                sections.append(self._crm_context())
            elif wants_tasks:
                sections.append(self._task_context())
            elif wants_projects:
                sections.append(self._project_context())
            else:
                # Default: light context always useful
                sections.append(self._task_context(limit=5))
                sections.append(self._project_context(limit=5))

        except Exception:
            logger.warning("Notion context enrichment failed", exc_info=True)
            return ""

        parts = [s for s in sections if s]
        return "\n\n".join(parts) if parts else ""

    def _task_context(self, limit: int = 10) -> str:
        db_id = self._db.get("tasks_db", "")
        if not db_id:
            return ""
        status_prop = self._p("tasks", "status")
        due_prop = self._p("tasks", "due_date")
        try:
            pages = self._client.query_database(
                db_id,
                filter={
                    "or": [
                        {"property": status_prop, "select": {"equals": "To Do"}},
                        {"property": status_prop, "select": {"equals": "In Progress"}},
                    ]
                },
                sorts=[{"property": due_prop, "direction": "ascending"}],
                page_size=limit,
            )
        except NotionAPIError:
            return ""

        if not pages:
            return "**Open Tasks**: none"

        lines = [f"**Open Tasks ({len(pages)})**"]
        for p in pages:
            title = _get_title(p)
            status = _get_select(p, status_prop)
            due = _get_date_start(p, due_prop)
            due_str = f", due: {due}" if due else ""
            lines.append(f"- {title} [{status}{due_str}]")
        return "\n".join(lines)

    def _project_context(self, limit: int = 8) -> str:
        db_id = self._db.get("projects_db", "")
        if not db_id:
            return ""
        status_prop = self._p("projects", "status")
        deadline_prop = self._p("projects", "deadline")
        try:
            pages = self._client.query_database(
                db_id,
                filter={"property": status_prop, "select": {"does_not_equal": "Done"}},
                sorts=[{"property": deadline_prop, "direction": "ascending"}],
                page_size=limit,
            )
        except NotionAPIError:
            return ""

        if not pages:
            return "**Active Projects**: none"

        lines = [f"**Active Projects ({len(pages)})**"]
        for p in pages:
            title = _get_title(p)
            status = _get_select(p, status_prop)
            deadline = _get_date_start(p, deadline_prop)
            deadline_str = f", deadline: {deadline}" if deadline else ""
            lines.append(f"- {title} [{status}{deadline_str}]")
        return "\n".join(lines)

    def _meeting_context(self, limit: int = 3) -> str:
        db_id = self._db.get("meeting_notes_db", "")
        if not db_id:
            return ""
        date_prop = self._p("meeting_notes", "date")
        try:
            pages = self._client.query_database(
                db_id,
                sorts=[{"property": date_prop, "direction": "descending"}],
                page_size=limit,
            )
        except NotionAPIError:
            return ""

        if not pages:
            return ""

        lines = ["**Recent Meeting Notes**"]
        for p in pages:
            title = _get_title(p)
            meeting_date = _get_date_start(p, date_prop)
            date_str = f" ({meeting_date})" if meeting_date else ""
            lines.append(f"- {title}{date_str}")
        return "\n".join(lines)

    def _crm_context(self) -> str:
        db_id = self._db.get("crm_db", "")
        if not db_id:
            return ""
        # Return a note that CRM is available; detailed lookup happens at step execution
        return "**CRM**: available — use find_contact action to look up specific people"

    # -----------------------------------------------------------------------
    # Entry point 2: Step execution
    # -----------------------------------------------------------------------

    def execute_step(self, step: dict) -> dict:
        """
        Dispatch a single action plan step to the correct handler.
        Always returns a dict — never raises.
        """
        action = step.get("action", "")
        params = step.get("params", {})
        description = step.get("description", action)

        handlers = {
            "create_task": self._create_task,
            "update_task_status": self._update_task_status,
            "create_project": self._create_project,
            "create_meeting_note": self._create_meeting_note,
            "create_journal_entry": self._create_journal_entry,
            "save_knowledge": self._save_knowledge,
            "update_crm_contact": self._update_crm_contact,
            "find_contact": self._find_contact,
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
    # Action handlers (private)
    # -----------------------------------------------------------------------

    def _create_task(self, params: dict) -> dict:
        db_id = self._db.get("tasks_db", "")
        if not db_id:
            return {"ok": False, "error": "tasks_db not configured"}

        properties: dict[str, Any] = {self._p("tasks", "name"): _title_prop(params["name"])}
        if status := params.get("status", "To Do"):
            properties[self._p("tasks", "status")] = _status_prop(status)
        if priority := params.get("priority"):
            properties[self._p("tasks", "priority")] = _select_prop(priority)
        if due_date := params.get("due_date"):
            properties[self._p("tasks", "due_date")] = _date_prop(due_date)
        if project_id := params.get("project_id"):
            properties[self._p("tasks", "project")] = _relation_prop(project_id)

        page = self._client.create_page(database_id=db_id, properties=properties)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _update_task_status(self, params: dict) -> dict:
        page_id = params.get("page_id", "")
        if not page_id:
            return {"ok": False, "error": "page_id required"}
        properties = {self._p("tasks", "status"): _status_prop(params["status"])}
        page = self._client.update_page(page_id=page_id, properties=properties)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _create_project(self, params: dict) -> dict:
        db_id = self._db.get("projects_db", "")
        if not db_id:
            return {"ok": False, "error": "projects_db not configured"}

        properties: dict[str, Any] = {self._p("projects", "name"): _title_prop(params["name"])}
        if status := params.get("status", "Planning"):
            properties[self._p("projects", "status")] = _status_prop(status)
        if deadline := params.get("deadline"):
            properties[self._p("projects", "deadline")] = _date_prop(deadline)
        if owner := params.get("owner"):
            properties[self._p("projects", "owner")] = _rich_text_prop(owner)

        page = self._client.create_page(database_id=db_id, properties=properties)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _create_meeting_note(self, params: dict) -> dict:
        db_id = self._db.get("meeting_notes_db", "")
        if not db_id:
            return {"ok": False, "error": "meeting_notes_db not configured"}

        properties: dict[str, Any] = {self._p("meeting_notes", "name"): _title_prop(params["name"])}
        if meeting_date := params.get("date"):
            properties[self._p("meeting_notes", "date")] = _date_prop(meeting_date)
        if attendees := params.get("attendees"):
            properties[self._p("meeting_notes", "attendees")] = _rich_text_prop(attendees)
        if project_id := params.get("project_id"):
            properties[self._p("meeting_notes", "project")] = _relation_prop(project_id)

        children = None
        if body := params.get("body"):
            children = [_paragraph_block(body)]

        page = self._client.create_page(database_id=db_id, properties=properties, children=children)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _create_journal_entry(self, params: dict) -> dict:
        db_id = self._db.get("journal_db", "")
        if not db_id:
            return {"ok": False, "error": "journal_db not configured"}

        entry_date = params.get("date", date_type.today().isoformat())
        template = params.get("template", "daily")

        properties: dict[str, Any] = {
            self._p("journal", "name"): _title_prop(f"Journal — {entry_date}"),
            self._p("journal", "date"): _date_prop(entry_date),
        }

        children: list[dict] = []
        if body := params.get("body"):
            children.append(_paragraph_block(body))
        if template == "weekly":
            children += [
                _heading2_block("Wins"),
                _paragraph_block(""),
                _heading2_block("Challenges"),
                _paragraph_block(""),
                _heading2_block("Next Week"),
                _paragraph_block(""),
            ]

        page = self._client.create_page(database_id=db_id, properties=properties, children=children or None)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _save_knowledge(self, params: dict) -> dict:
        db_id = self._db.get("knowledge_base_db", "")
        if not db_id:
            return {"ok": False, "error": "knowledge_base_db not configured"}

        properties: dict[str, Any] = {self._p("knowledge_base", "name"): _title_prop(params["name"])}
        if tags := params.get("tags"):
            properties[self._p("knowledge_base", "tags")] = _multi_select_prop(tags)

        children = None
        if body := params.get("body"):
            children = [_paragraph_block(body)]

        page = self._client.create_page(database_id=db_id, properties=properties, children=children)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _update_crm_contact(self, params: dict) -> dict:
        page_id = params.get("page_id", "")
        if not page_id:
            return {"ok": False, "error": "page_id required"}

        properties: dict[str, Any] = {}
        if last_contacted := params.get("last_contacted"):
            properties[self._p("crm", "last_contacted")] = _date_prop(last_contacted)
        if notes := params.get("notes"):
            properties[self._p("crm", "notes")] = _rich_text_prop(notes)

        if not properties:
            return {"ok": False, "error": "No fields to update"}

        page = self._client.update_page(page_id=page_id, properties=properties)
        return {"ok": True, "page_id": page["id"], "url": _get_url(page)}

    def _find_contact(self, params: dict) -> dict:
        query = params.get("query", "")
        if not query:
            return {"ok": False, "error": "query required"}

        try:
            results = self._client.search_pages(query, page_size=5)
        except NotionAPIError as e:
            return {"ok": False, "error": str(e)}

        contacts = [
            {
                "id": p["id"],
                "name": _get_title(p),
                "url": _get_url(p),
            }
            for p in results
            if p.get("object") == "page"
        ]
        return {"ok": True, "contacts": contacts}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_db_id(value: str) -> str:
    """
    Accept a raw Notion DB ID or a full Notion URL and return just the UUID.

    Handles formats:
      - "abc123def456..."                   (bare 32-char hex)
      - "abc123de-f456-..."                 (hyphenated UUID)
      - "https://www.notion.so/Title-<id>"  (page URL with title prefix)
      - "https://www.notion.so/<id>?v=..."  (database URL with view param)
    """
    if not value:
        return ""
    # Extract a 32-char hex block (with optional hyphens) from anywhere in the string
    match = re.search(r"([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})", value, re.IGNORECASE)
    if match:
        return match.group(1)
    return value  # return as-is if no UUID pattern found


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_second_brain(config: dict) -> "NotionSecondBrain | None":
    """
    Build and return a NotionSecondBrain, or None if Notion is not configured.
    Called once from Orchestrator.__init__().
    """
    if not config.get("integrations", {}).get("notion", {}).get("enabled", False):
        return None

    token = os.environ.get("NOTION_TOKEN", "")
    if not token:
        logger.warning("Notion integration enabled but NOTION_TOKEN is not set")
        return None

    # Load DB IDs from config file
    db_config_path = Path(__file__).parent.parent.parent / "config" / "notion_databases.yaml"
    try:
        with open(db_config_path) as f:
            raw: dict = yaml.safe_load(f).get("notion", {})
    except FileNotFoundError:
        logger.warning("notion_databases.yaml not found at %s", db_config_path)
        raw = {}

    # Separate DB IDs from property mappings
    props = raw.pop("properties", None)

    # Extract UUIDs from URLs (users often paste the full Notion URL)
    db_ids = {key: _extract_db_id(val or "") for key, val in raw.items()}

    # Log each DB so misconfiguration is immediately visible
    for key, val in db_ids.items():
        if val:
            logger.info("Notion DB %-25s → %s", key, val)
        else:
            logger.warning("Notion DB %-25s → NOT CONFIGURED (some features unavailable)", key)

    client = NotionClient(token)
    configured = sum(1 for v in db_ids.values() if v)
    logger.info("Notion Second Brain connected (%d/%d DBs configured)", configured, len(db_ids))
    return NotionSecondBrain(client=client, db_ids=db_ids, props=props)
