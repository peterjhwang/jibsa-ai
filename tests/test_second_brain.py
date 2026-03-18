"""Tests for NotionSecondBrain — all Notion API calls are mocked."""
import json
import pytest
from unittest.mock import MagicMock, patch, call

from src.integrations.notion_client import NotionAPIError, NotionClient
from src.integrations.notion_db_registry import DatabaseRegistry
from src.integrations.notion_second_brain import (
    NotionSecondBrain,
    _flatten_page,
    _title_prop,
    _select_prop,
    _status_prop,
    _date_prop,
    _multi_select_prop,
    build_user_second_brain,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TASKS_DB = "aaaaaaaa-0000-0000-0000-000000000001"
PROJECTS_DB = "bbbbbbbb-0000-0000-0000-000000000002"
NOTES_DB = "cccccccc-0000-0000-0000-000000000003"
EXPENSE_DB = "dddddddd-0000-0000-0000-000000000004"
JOURNAL_DB = "eeeeeeee-0000-0000-0000-000000000005"

DATABASES = [
    {"name": "Tasks",           "id": TASKS_DB,    "keywords": ["task", "todo"]},
    {"name": "Projects",        "id": PROJECTS_DB, "keywords": ["project"]},
    {"name": "Notes",           "id": NOTES_DB,    "keywords": ["note", "knowledge"]},
    {"name": "Expense Record",  "id": EXPENSE_DB,  "keywords": ["expense", "spending"]},
    {"name": "Journal Entries", "id": JOURNAL_DB,  "keywords": ["journal"]},
]

TASKS_SCHEMA = {
    "Name":       {"type": "title"},
    "Status":     {"type": "status"},
    "Priority ":  {"type": "select"},
    "Due Date ":  {"type": "date"},
}

PROJECTS_SCHEMA = {
    "Name":      {"type": "title"},
    "Status":    {"type": "status"},
    "End Date ": {"type": "date"},
}

NOTES_SCHEMA = {
    "Name": {"type": "title"},
    "Tags": {"type": "multi_select"},
}

EXPENSE_SCHEMA = {
    "Expense Record": {"type": "title"},
    "Amount":         {"type": "number"},
    "Date":           {"type": "date"},
    "Note":           {"type": "rich_text"},
}

JOURNAL_SCHEMA = {
    "Name": {"type": "title"},
    "Date": {"type": "date"},
}


def make_brain(mock_client=None, parent_page_id="") -> tuple[NotionSecondBrain, MagicMock]:
    if mock_client is None:
        mock_client = MagicMock(spec=NotionClient)
    registry = DatabaseRegistry.from_yaml(DATABASES)
    brain = NotionSecondBrain(client=mock_client, db_registry=registry, parent_page_id=parent_page_id)
    brain._schema_cache = {
        TASKS_DB:    TASKS_SCHEMA,
        PROJECTS_DB: PROJECTS_SCHEMA,
        NOTES_DB:    NOTES_SCHEMA,
        EXPENSE_DB:  EXPENSE_SCHEMA,
        JOURNAL_DB:  JOURNAL_SCHEMA,
    }
    return brain, mock_client


# ---------------------------------------------------------------------------
# _flatten_page
# ---------------------------------------------------------------------------

def test_flatten_page_extracts_title():
    page = {"id": "p1", "url": "https://notion.so/p1", "properties": {
        "Name": {"type": "title", "title": [{"plain_text": "Hello"}]},
    }}
    result = _flatten_page(page)
    assert result["Name"] == "Hello"
    assert result["_id"] == "p1"


def test_flatten_page_strips_trailing_spaces_from_keys():
    page = {"id": "p1", "url": "", "properties": {
        "Priority ": {"type": "select", "select": {"name": "High"}},
        "Due Date ": {"type": "date", "date": {"start": "2026-03-10"}},
    }}
    result = _flatten_page(page)
    assert "Priority" in result
    assert "Due Date" in result


def test_flatten_page_handles_status_type():
    page = {"id": "p1", "url": "", "properties": {
        "Status": {"type": "status", "status": {"name": "In Progress"}},
    }}
    assert _flatten_page(page)["Status"] == "In Progress"


def test_flatten_page_skips_empty_values():
    page = {"id": "p1", "url": "", "properties": {
        "Name": {"type": "title", "title": []},
        "Note": {"type": "rich_text", "rich_text": []},
    }}
    result = _flatten_page(page)
    assert "Name" not in result
    assert "Note" not in result


def test_flatten_page_handles_multi_select():
    page = {"id": "p1", "url": "", "properties": {
        "Tags": {"type": "multi_select", "multi_select": [{"name": "A"}, {"name": "B"}]},
    }}
    assert _flatten_page(page)["Tags"] == ["A", "B"]


def test_flatten_page_handles_checkbox():
    page = {"id": "p1", "url": "", "properties": {
        "Done": {"type": "checkbox", "checkbox": True},
    }}
    assert _flatten_page(page)["Done"] is True


# ---------------------------------------------------------------------------
# Property builders
# ---------------------------------------------------------------------------

def test_title_prop():
    assert _title_prop("Hello") == {"title": [{"text": {"content": "Hello"}}]}

def test_select_prop():
    assert _select_prop("High") == {"select": {"name": "High"}}

def test_status_prop():
    assert _status_prop("Done") == {"status": {"name": "Done"}}

def test_date_prop():
    assert _date_prop("2026-03-10") == {"date": {"start": "2026-03-10"}}

def test_multi_select_prop():
    assert _multi_select_prop(["A", "B"]) == {"multi_select": [{"name": "A"}, {"name": "B"}]}


# ---------------------------------------------------------------------------
# Schema discovery helpers
# ---------------------------------------------------------------------------

def test_title_prop_name_finds_title():
    brain, _ = make_brain()
    assert brain._title_prop_name(TASKS_SCHEMA) == "Name"


def test_prop_by_type_finds_status():
    brain, _ = make_brain()
    assert brain._prop_by_type(TASKS_SCHEMA, "status") == "Status"


def test_prop_by_keyword_finds_priority():
    brain, _ = make_brain()
    result = brain._prop_by_keyword(TASKS_SCHEMA, "priority")
    assert result == "Priority "  # trailing space from template


def test_prop_by_keyword_finds_due():
    brain, _ = make_brain()
    result = brain._prop_by_keyword(TASKS_SCHEMA, "due")
    assert result == "Due Date "


# ---------------------------------------------------------------------------
# Context enrichment — keyword routing
# ---------------------------------------------------------------------------

def test_context_routes_to_tasks_on_task_keyword():
    brain, mock_client = make_brain()
    mock_client.query_database.return_value = []
    brain.get_context_for_request("what tasks do I have?")
    called_ids = [call.args[0] for call in mock_client.query_database.call_args_list]
    assert TASKS_DB in called_ids


def test_context_routes_to_projects_on_project_keyword():
    brain, mock_client = make_brain()
    mock_client.query_database.return_value = []
    brain.get_context_for_request("show me my projects")
    called_ids = [call.args[0] for call in mock_client.query_database.call_args_list]
    assert PROJECTS_DB in called_ids


def test_context_defaults_to_tasks_and_projects():
    brain, mock_client = make_brain()
    mock_client.query_database.return_value = []
    brain.get_context_for_request("what's going on?")
    called_ids = [call.args[0] for call in mock_client.query_database.call_args_list]
    assert TASKS_DB in called_ids
    assert PROJECTS_DB in called_ids


def test_context_returns_json_with_flattened_pages():
    brain, mock_client = make_brain()
    mock_client.query_database.return_value = [{
        "id": "p1", "url": "https://notion.so/p1",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Fix bug"}]},
            "Status": {"type": "status", "status": {"name": "To Do"}},
        }
    }]
    result = brain.get_context_for_request("show tasks")
    assert "Fix bug" in result
    assert "To Do" in result


def test_context_returns_empty_string_on_api_error():
    brain, mock_client = make_brain()
    mock_client.query_database.side_effect = NotionAPIError("query", Exception("boom"))
    result = brain.get_context_for_request("show tasks")
    assert result == ""


def test_context_caps_at_4_databases():
    many_dbs = [{"name": f"DB{i}", "id": f"{'a'*8}-{'0'*4}-{'0'*4}-{'0'*4}-{str(i).zfill(12)}", "keywords": ["test"]}
                for i in range(6)]
    registry = DatabaseRegistry.from_yaml(many_dbs)
    brain = NotionSecondBrain(client=MagicMock(spec=NotionClient), db_registry=registry)
    brain._client.query_database.return_value = []
    brain.get_context_for_request("test test test")
    assert brain._client.query_database.call_count <= 4


# ---------------------------------------------------------------------------
# execute_step — create_task
# ---------------------------------------------------------------------------

def test_create_task_returns_ok():
    brain, mock_client = make_brain()
    mock_client.create_page.return_value = {"id": "page1", "url": "https://notion.so/page1"}
    result = brain.execute_step({"action": "create_task", "params": {"name": "Write tests"}})
    assert result["ok"] is True
    assert result["page_id"] == "page1"


def test_create_task_uses_schema_discovered_property_names():
    brain, mock_client = make_brain()
    mock_client.create_page.return_value = {"id": "p1", "url": ""}
    brain.execute_step({"action": "create_task", "params": {
        "name": "Write tests", "status": "To Do", "priority": "High", "due_date": "2026-03-10"
    }})
    props = mock_client.create_page.call_args.kwargs["properties"]
    assert props["Name"] == _title_prop("Write tests")
    assert props["Status"] == _status_prop("To Do")
    assert props["Priority "] == _select_prop("High")
    assert props["Due Date "] == _date_prop("2026-03-10")


def test_create_task_returns_error_when_db_not_configured():
    registry = DatabaseRegistry()
    brain = NotionSecondBrain(client=MagicMock(), db_registry=registry)
    result = brain.execute_step({"action": "create_task", "params": {"name": "x"}})
    assert result["ok"] is False
    assert "not configured" in result["error"]


# ---------------------------------------------------------------------------
# execute_step — update_task_status
# ---------------------------------------------------------------------------

def test_update_task_status_sends_only_status_property():
    brain, mock_client = make_brain()
    mock_client.update_page.return_value = {"id": "p1", "url": ""}
    brain.execute_step({"action": "update_task_status", "params": {"page_id": "p1", "status": "Done"}})
    props = mock_client.update_page.call_args.kwargs["properties"]
    assert list(props.keys()) == ["Status"]
    assert props["Status"] == _status_prop("Done")


def test_update_task_status_requires_page_id():
    brain, _ = make_brain()
    result = brain.execute_step({"action": "update_task_status", "params": {"status": "Done"}})
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# execute_step — create_note (weekly template)
# ---------------------------------------------------------------------------

def test_create_note_with_body_sends_children():
    brain, mock_client = make_brain()
    mock_client.create_page.return_value = {"id": "p1", "url": ""}
    brain.execute_step({"action": "create_note", "params": {"name": "My note", "body": "Content here"}})
    kwargs = mock_client.create_page.call_args.kwargs
    assert kwargs["children"] is not None
    assert len(kwargs["children"]) == 1


def test_create_journal_weekly_has_three_headings():
    brain, mock_client = make_brain()
    mock_client.create_page.return_value = {"id": "p1", "url": ""}
    brain.execute_step({"action": "create_journal_entry", "params": {"template": "weekly"}})
    children = mock_client.create_page.call_args.kwargs["children"]
    headings = [b for b in children if b.get("type") == "heading_2"]
    assert len(headings) == 3


# ---------------------------------------------------------------------------
# execute_step — unknown action
# ---------------------------------------------------------------------------

def test_unknown_action_returns_error():
    brain, _ = make_brain()
    result = brain.execute_step({"action": "fly_to_moon", "params": {}})
    assert result["ok"] is False
    assert "fly_to_moon" in result["error"]


def test_api_failure_returns_error_dict_not_exception():
    brain, mock_client = make_brain()
    mock_client.create_page.side_effect = NotionAPIError("create_page", Exception("500"))
    result = brain.execute_step({"action": "create_task", "params": {"name": "x"}})
    assert result["ok"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# build_user_second_brain factory
# ---------------------------------------------------------------------------

def test_build_user_returns_none_when_no_token():
    mock_oauth = MagicMock()
    mock_oauth.get_token.return_value = None
    mock_registry = MagicMock()
    result = build_user_second_brain("U123", mock_oauth, mock_registry, {})
    assert result is None


def test_build_user_returns_brain_when_token_exists():
    mock_oauth = MagicMock()
    mock_oauth.get_token.return_value = "ntn_test_token"
    mock_registry = MagicMock()
    mock_registry.get_registry.return_value = DatabaseRegistry()
    mock_registry.get_parent_page_id.return_value = ""
    with patch("src.integrations.notion_second_brain.NotionClient"):
        result = build_user_second_brain("U123", mock_oauth, mock_registry, {})
    assert result is not None
    assert isinstance(result, NotionSecondBrain)


# ---------------------------------------------------------------------------
# _ensure_db — lazy creation
# ---------------------------------------------------------------------------

def test_ensure_db_returns_existing_id():
    brain, _ = make_brain()
    assert brain._ensure_db("Tasks") == TASKS_DB


def test_ensure_db_returns_empty_without_parent_page():
    registry = DatabaseRegistry()
    brain = NotionSecondBrain(client=MagicMock(), db_registry=registry)
    assert brain._ensure_db("Tasks") == ""


def test_ensure_db_creates_from_template():
    mock_client = MagicMock(spec=NotionClient)
    mock_client.create_database.return_value = {"id": "new-db-id", "url": ""}
    registry = DatabaseRegistry()
    brain = NotionSecondBrain(client=mock_client, db_registry=registry, parent_page_id="parent-123")
    result = brain._ensure_db("Tasks")
    assert result == "new-db-id"
    mock_client.create_database.assert_called_once()
    # Should be registered now
    assert registry.get_db_id("Tasks") == "new-db-id"


def test_ensure_db_returns_empty_for_unknown_template():
    registry = DatabaseRegistry()
    brain = NotionSecondBrain(client=MagicMock(), db_registry=registry, parent_page_id="parent-123")
    assert brain._ensure_db("SomeRandomDB") == ""


def test_ensure_db_returns_empty_on_api_error():
    mock_client = MagicMock(spec=NotionClient)
    mock_client.create_database.side_effect = NotionAPIError("create_database", Exception("403"))
    registry = DatabaseRegistry()
    brain = NotionSecondBrain(client=mock_client, db_registry=registry, parent_page_id="parent-123")
    assert brain._ensure_db("Tasks") == ""


# ---------------------------------------------------------------------------
# New action handlers
# ---------------------------------------------------------------------------

def test_create_database_action():
    mock_client = MagicMock(spec=NotionClient)
    mock_client.create_database.return_value = {"id": "new-db", "url": "https://notion.so/new-db"}
    brain, _ = make_brain(mock_client, parent_page_id="parent-123")
    result = brain.execute_step({"action": "create_database", "params": {
        "name": "CRM Leads",
        "properties": [
            {"name": "Name", "type": "title"},
            {"name": "Company", "type": "rich_text"},
            {"name": "Status", "type": "select", "options": ["New", "Contacted", "Won"]},
        ],
        "keywords": ["crm", "lead"],
    }})
    assert result["ok"] is True
    assert result["database_id"] == "new-db"
    # Should be registered
    assert brain._registry.get_db_id("CRM Leads") == "new-db"


def test_create_database_requires_parent_page():
    brain, _ = make_brain()  # no parent_page_id
    result = brain.execute_step({"action": "create_database", "params": {"name": "Test"}})
    assert result["ok"] is False
    assert "parent page" in result["error"].lower()


def test_create_database_requires_name():
    brain, _ = make_brain(parent_page_id="parent-123")
    result = brain.execute_step({"action": "create_database", "params": {}})
    assert result["ok"] is False
    assert "name" in result["error"].lower()


def test_create_entry_action():
    brain, mock_client = make_brain()
    mock_client.create_page.return_value = {"id": "p1", "url": "https://notion.so/p1"}
    result = brain.execute_step({"action": "create_entry", "params": {
        "database": "Tasks",
        "title": "New task",
        "properties": {"status": "To Do", "priority": "High"},
    }})
    assert result["ok"] is True
    assert result["page_id"] == "p1"


def test_create_entry_requires_database_name():
    brain, _ = make_brain()
    result = brain.execute_step({"action": "create_entry", "params": {"title": "x"}})
    assert result["ok"] is False
    assert "database" in result["error"].lower()


def test_create_standalone_page_action():
    mock_client = MagicMock(spec=NotionClient)
    mock_client.create_page_under_page.return_value = {"id": "page1", "url": "https://notion.so/page1"}
    brain, _ = make_brain(mock_client, parent_page_id="parent-123")
    result = brain.execute_step({"action": "create_standalone_page", "params": {
        "title": "Meeting Notes",
        "content": "Discussed Q2 goals.",
    }})
    assert result["ok"] is True
    assert result["page_id"] == "page1"
    mock_client.create_page_under_page.assert_called_once()


def test_create_standalone_page_requires_parent():
    brain, _ = make_brain()
    result = brain.execute_step({"action": "create_standalone_page", "params": {"title": "Test"}})
    assert result["ok"] is False


def test_add_page_content_action():
    mock_client = MagicMock(spec=NotionClient)
    mock_client.append_blocks.return_value = {}
    brain, _ = make_brain(mock_client)
    result = brain.execute_step({"action": "add_page_content", "params": {
        "page_id": "page-123",
        "content": "New content here.",
    }})
    assert result["ok"] is True
    mock_client.append_blocks.assert_called_once()


def test_add_page_content_requires_page_id():
    brain, _ = make_brain()
    result = brain.execute_step({"action": "add_page_content", "params": {"content": "x"}})
    assert result["ok"] is False


def test_add_page_content_requires_content():
    brain, _ = make_brain()
    result = brain.execute_step({"action": "add_page_content", "params": {"page_id": "p1"}})
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# Lazy creation through existing handlers
# ---------------------------------------------------------------------------

def test_create_task_auto_creates_db_with_parent_page():
    mock_client = MagicMock(spec=NotionClient)
    mock_client.create_database.return_value = {"id": "auto-tasks-db"}
    mock_client.get_database_schema.return_value = {"Name": {"type": "title"}}
    mock_client.create_page.return_value = {"id": "p1", "url": ""}
    registry = DatabaseRegistry()
    brain = NotionSecondBrain(client=mock_client, db_registry=registry, parent_page_id="parent-123")
    result = brain.execute_step({"action": "create_task", "params": {"name": "Auto task"}})
    assert result["ok"] is True
    mock_client.create_database.assert_called_once()
