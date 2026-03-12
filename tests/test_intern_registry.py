"""Tests for InternRegistry — CRUD backed by Notion."""
from unittest.mock import MagicMock, patch
import pytest

from src.intern_registry import InternRegistry
from src.integrations.notion_client import NotionAPIError, NotionClient
from src.integrations.notion_second_brain import NotionSecondBrain
from src.models.intern import InternJD


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

INTERNS_DB = "iiiiiii-0000-0000-0000-000000000099"

DATABASES = [
    {"name": "Interns", "id": INTERNS_DB, "keywords": ["intern"]},
    {"name": "Tasks", "id": "aaaa-0000-0000-0000-000000000001", "keywords": ["task"]},
]

INTERNS_SCHEMA = {
    "Name":             {"type": "title"},
    "Role":             {"type": "rich_text"},
    "Responsibilities": {"type": "rich_text"},
    "Tone":             {"type": "rich_text"},
    "Tools Allowed":    {"type": "multi_select"},
    "Autonomy Rules":   {"type": "rich_text"},
    "Created By":       {"type": "rich_text"},
    "Active":           {"type": "checkbox"},
}


def make_registry(mock_client=None) -> tuple[InternRegistry, MagicMock]:
    if mock_client is None:
        mock_client = MagicMock(spec=NotionClient)
    brain = NotionSecondBrain(client=mock_client, databases=DATABASES)
    brain._schema_cache = {INTERNS_DB: INTERNS_SCHEMA}
    registry = InternRegistry(brain, {})
    return registry, mock_client


def make_notion_intern_page(name="Alex", role="Content Intern", active=True):
    """Create a mock Notion page representing an intern."""
    return {
        "id": f"page-{name.lower()}",
        "url": f"https://notion.so/page-{name.lower()}",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": name}]},
            "Role": {"type": "rich_text", "rich_text": [{"plain_text": role}]},
            "Responsibilities": {"type": "rich_text", "rich_text": [{"plain_text": "Write posts\nTrack metrics"}]},
            "Tone": {"type": "rich_text", "rich_text": [{"plain_text": "Professional"}]},
            "Tools Allowed": {"type": "multi_select", "multi_select": [{"name": "notion"}]},
            "Autonomy Rules": {"type": "rich_text", "rich_text": [{"plain_text": "Always propose"}]},
            "Created By": {"type": "rich_text", "rich_text": [{"plain_text": "U001"}]},
            "Active": {"type": "checkbox", "checkbox": active},
        },
    }


# ---------------------------------------------------------------------------
# list_interns
# ---------------------------------------------------------------------------

def test_list_interns_queries_notion():
    registry, mock_client = make_registry()
    mock_client.query_database.return_value = [make_notion_intern_page("Alex")]
    interns = registry.list_interns()
    assert len(interns) == 1
    assert interns[0].name == "Alex"
    assert interns[0].role == "Content Intern"
    assert interns[0].tools_allowed == ["notion"]
    assert interns[0].responsibilities == ["Write posts", "Track metrics"]


def test_list_interns_uses_cache():
    registry, mock_client = make_registry()
    mock_client.query_database.return_value = [make_notion_intern_page("Alex")]
    registry.list_interns()
    registry.list_interns()
    assert mock_client.query_database.call_count == 1  # only called once due to cache


def test_list_interns_force_refresh():
    registry, mock_client = make_registry()
    mock_client.query_database.return_value = [make_notion_intern_page("Alex")]
    registry.list_interns()
    registry.list_interns(force_refresh=True)
    assert mock_client.query_database.call_count == 2


def test_list_interns_filters_inactive():
    registry, mock_client = make_registry()
    mock_client.query_database.return_value = [
        make_notion_intern_page("Alex", active=True),
        make_notion_intern_page("Bob", active=False),
    ]
    interns = registry.list_interns()
    assert len(interns) == 1
    assert interns[0].name == "Alex"


def test_list_interns_returns_empty_on_api_error():
    registry, mock_client = make_registry()
    mock_client.query_database.side_effect = NotionAPIError("query", Exception("500"))
    interns = registry.list_interns()
    assert interns == []


def test_list_interns_empty_when_no_db():
    brain = MagicMock()
    brain._get_db_id.return_value = ""
    registry = InternRegistry(brain, {})
    assert registry.list_interns() == []


# ---------------------------------------------------------------------------
# get_intern
# ---------------------------------------------------------------------------

def test_get_intern_case_insensitive():
    registry, mock_client = make_registry()
    mock_client.query_database.return_value = [make_notion_intern_page("Alex")]
    intern = registry.get_intern("ALEX")
    assert intern is not None
    assert intern.name == "Alex"


def test_get_intern_returns_none_for_unknown():
    registry, mock_client = make_registry()
    mock_client.query_database.return_value = [make_notion_intern_page("Alex")]
    assert registry.get_intern("Bob") is None


# ---------------------------------------------------------------------------
# create_intern
# ---------------------------------------------------------------------------

def test_create_intern_success():
    registry, mock_client = make_registry()
    mock_client.query_database.return_value = []  # no existing interns
    mock_client.create_page.return_value = {"id": "page-new", "url": "https://notion.so/page-new"}

    jd = InternJD(
        name="Mia", role="Dev Helper", responsibilities=["Review PRs"],
        tone="Friendly", tools_allowed=["notion"], autonomy_rules="Always propose",
        created_by="U002",
    )
    result = registry.create_intern(jd)
    assert result["ok"] is True
    assert result["page_id"] == "page-new"
    assert jd.notion_page_id == "page-new"


def test_create_intern_duplicate_name():
    registry, mock_client = make_registry()
    mock_client.query_database.return_value = [make_notion_intern_page("Alex")]

    jd = InternJD(
        name="Alex", role="Duplicate", responsibilities=[],
        tone="", tools_allowed=[], autonomy_rules="",
    )
    result = registry.create_intern(jd)
    assert result["ok"] is False
    assert "already exists" in result["error"]


def test_create_intern_no_db():
    brain = NotionSecondBrain(client=MagicMock(), databases=[])
    registry = InternRegistry(brain, {})
    jd = InternJD(name="X", role="X", responsibilities=[], tone="", tools_allowed=[], autonomy_rules="")
    result = registry.create_intern(jd)
    assert result["ok"] is False
    assert "not configured" in result["error"]


# ---------------------------------------------------------------------------
# get_intern_names
# ---------------------------------------------------------------------------

def test_get_intern_names():
    registry, mock_client = make_registry()
    mock_client.query_database.return_value = [
        make_notion_intern_page("Alex"),
        make_notion_intern_page("Mia"),
    ]
    names = registry.get_intern_names()
    assert "alex" in names
    assert "mia" in names
