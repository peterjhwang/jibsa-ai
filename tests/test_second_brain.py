"""Tests for NotionSecondBrain — all Notion API calls are mocked."""
import pytest
from unittest.mock import ANY, MagicMock

from src.integrations.notion_client import NotionAPIError, NotionClient
from src.integrations.notion_second_brain import (
    NotionSecondBrain,
    _date_prop,
    _multi_select_prop,
    _relation_prop,
    _rich_text_prop,
    _select_prop,
    _status_prop,
    _title_prop,
    build_second_brain,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DB_IDS = {
    "tasks_db": "db-tasks-001",
    "projects_db": "db-projects-001",
    "meeting_notes_db": "db-meetings-001",
    "journal_db": "db-journal-001",
    "knowledge_base_db": "db-kb-001",
    "crm_db": "db-crm-001",
}


@pytest.fixture
def mock_client():
    return MagicMock(spec=NotionClient)


@pytest.fixture
def brain(mock_client):
    return NotionSecondBrain(client=mock_client, db_ids=DB_IDS)


def _fake_page(page_id="page-001", url="https://notion.so/page-001"):
    return {"id": page_id, "url": url, "object": "page", "properties": {}}


# ---------------------------------------------------------------------------
# Property builder tests (pure functions)
# ---------------------------------------------------------------------------


def test_title_prop():
    assert _title_prop("My Task") == {"title": [{"text": {"content": "My Task"}}]}


def test_select_prop():
    assert _select_prop("In Progress") == {"select": {"name": "In Progress"}}


def test_date_prop():
    assert _date_prop("2026-03-08") == {"date": {"start": "2026-03-08"}}


def test_relation_prop():
    assert _relation_prop("page-abc") == {"relation": [{"id": "page-abc"}]}


def test_rich_text_prop():
    result = _rich_text_prop("hello")
    assert result["rich_text"][0]["text"]["content"] == "hello"


def test_multi_select_prop():
    result = _multi_select_prop(["A", "B"])
    assert result == {"multi_select": [{"name": "A"}, {"name": "B"}]}


# ---------------------------------------------------------------------------
# get_context_for_request
# ---------------------------------------------------------------------------


def test_context_returns_empty_on_api_error(brain, mock_client):
    mock_client.query_database.side_effect = NotionAPIError("query", Exception("401"))
    result = brain.get_context_for_request("show me my tasks")
    assert result == ""


def test_context_queries_tasks_db_on_task_keyword(brain, mock_client):
    mock_client.query_database.return_value = []
    brain.get_context_for_request("what tasks do I have?")
    mock_client.query_database.assert_called_once_with(
        "db-tasks-001", filter=ANY, sorts=ANY, page_size=10
    )


def test_context_queries_projects_db_on_project_keyword(brain, mock_client):
    mock_client.query_database.return_value = []
    brain.get_context_for_request("show me active projects")
    mock_client.query_database.assert_called_once_with(
        "db-projects-001", filter=ANY, sorts=ANY, page_size=8
    )


def test_context_default_queries_both_tasks_and_projects(brain, mock_client):
    mock_client.query_database.return_value = []
    brain.get_context_for_request("hello")
    assert mock_client.query_database.call_count == 2


def test_context_returns_empty_when_db_id_missing(mock_client):
    empty_brain = NotionSecondBrain(client=mock_client, db_ids={})
    result = empty_brain.get_context_for_request("tasks?")
    assert result == ""
    mock_client.query_database.assert_not_called()


def test_context_includes_task_title_in_output(brain, mock_client):
    mock_client.query_database.return_value = [
        {
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Fix the bug"}]},
                "Status": {"select": {"name": "In Progress"}},
                "Due Date": {"date": None},
            }
        }
    ]
    result = brain.get_context_for_request("my tasks")
    assert "Fix the bug" in result


# ---------------------------------------------------------------------------
# execute_step — create_task
# ---------------------------------------------------------------------------


def test_create_task_returns_ok_and_page_id(brain, mock_client):
    mock_client.create_page.return_value = _fake_page("page-123")
    result = brain.execute_step({
        "service": "notion",
        "action": "create_task",
        "params": {"name": "Write tests", "status": "To Do", "priority": "High"},
        "description": "Create a task",
    })
    assert result["ok"] is True
    assert result["page_id"] == "page-123"


def test_create_task_sends_correct_properties(brain, mock_client):
    mock_client.create_page.return_value = _fake_page()
    brain.execute_step({
        "service": "notion",
        "action": "create_task",
        "params": {"name": "Write tests", "status": "To Do", "priority": "High", "due_date": "2026-03-10"},
        "description": "Create task",
    })
    props = mock_client.create_page.call_args.kwargs["properties"]
    assert props["Name"] == _title_prop("Write tests")
    assert props["Status"] == _status_prop("To Do")
    assert props["Priority"] == _select_prop("High")
    assert props["Due Date"] == _date_prop("2026-03-10")


def test_create_task_returns_error_when_db_not_configured(mock_client):
    b = NotionSecondBrain(client=mock_client, db_ids={})
    result = b.execute_step({"action": "create_task", "params": {"name": "x"}, "description": "t"})
    assert result["ok"] is False
    assert "not configured" in result["error"]
    mock_client.create_page.assert_not_called()


# ---------------------------------------------------------------------------
# execute_step — update_task_status
# ---------------------------------------------------------------------------


def test_update_task_status_sends_only_status_property(brain, mock_client):
    mock_client.update_page.return_value = _fake_page("page-456")
    result = brain.execute_step({
        "action": "update_task_status",
        "params": {"page_id": "page-456", "status": "Done"},
        "description": "Mark done",
    })
    assert result["ok"] is True
    props = mock_client.update_page.call_args.kwargs["properties"]
    assert list(props.keys()) == ["Status"]
    assert props["Status"] == _status_prop("Done")


def test_update_task_status_requires_page_id(brain, mock_client):
    result = brain.execute_step({
        "action": "update_task_status",
        "params": {"status": "Done"},
        "description": "Missing page_id",
    })
    assert result["ok"] is False
    mock_client.update_page.assert_not_called()


# ---------------------------------------------------------------------------
# execute_step — create_meeting_note
# ---------------------------------------------------------------------------


def test_create_meeting_note_with_body_passes_children(brain, mock_client):
    mock_client.create_page.return_value = _fake_page()
    brain.execute_step({
        "action": "create_meeting_note",
        "params": {"name": "Sync with Alice", "date": "2026-03-08", "body": "Discussed roadmap"},
        "description": "Meeting note",
    })
    kwargs = mock_client.create_page.call_args.kwargs
    assert kwargs["children"] is not None
    assert len(kwargs["children"]) == 1


def test_create_meeting_note_without_body_passes_no_children(brain, mock_client):
    mock_client.create_page.return_value = _fake_page()
    brain.execute_step({
        "action": "create_meeting_note",
        "params": {"name": "Sync", "date": "2026-03-08"},
        "description": "Meeting note",
    })
    kwargs = mock_client.create_page.call_args.kwargs
    assert kwargs.get("children") is None


# ---------------------------------------------------------------------------
# execute_step — create_journal_entry
# ---------------------------------------------------------------------------


def test_create_journal_weekly_template_has_three_heading_sections(brain, mock_client):
    mock_client.create_page.return_value = _fake_page()
    brain.execute_step({
        "action": "create_journal_entry",
        "params": {"date": "2026-03-08", "template": "weekly"},
        "description": "Weekly review",
    })
    children = mock_client.create_page.call_args.kwargs["children"]
    headings = [b for b in children if b.get("type") == "heading_2"]
    assert len(headings) == 3
    heading_texts = [h["heading_2"]["rich_text"][0]["text"]["content"] for h in headings]
    assert "Wins" in heading_texts
    assert "Challenges" in heading_texts
    assert "Next Week" in heading_texts


# ---------------------------------------------------------------------------
# execute_step — unknown action
# ---------------------------------------------------------------------------


def test_unknown_action_returns_error_without_calling_api(brain, mock_client):
    result = brain.execute_step({
        "action": "teleport_task",
        "params": {},
        "description": "Should fail",
    })
    assert result["ok"] is False
    assert "Unknown action" in result["error"]
    mock_client.create_page.assert_not_called()
    mock_client.update_page.assert_not_called()


# ---------------------------------------------------------------------------
# execute_step — API failure returns error dict (does not raise)
# ---------------------------------------------------------------------------


def test_api_failure_returns_error_dict_not_exception(brain, mock_client):
    mock_client.create_page.side_effect = NotionAPIError("create", Exception("403 Forbidden"))
    result = brain.execute_step({
        "action": "create_task",
        "params": {"name": "Will fail"},
        "description": "Should not raise",
    })
    assert result["ok"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# build_second_brain factory
# ---------------------------------------------------------------------------


def test_build_returns_none_when_disabled():
    result = build_second_brain({"integrations": {"notion": {"enabled": False}}})
    assert result is None


def test_build_returns_none_when_token_missing(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    result = build_second_brain({"integrations": {"notion": {"enabled": True}}})
    assert result is None
