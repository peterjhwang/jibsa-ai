"""Tests for InternRegistry — CRUD backed by SQLite."""
import pytest

from src.intern_registry import InternRegistry
from src.integrations.intern_store import InternStore
from src.models.intern import InternJD


@pytest.fixture
def store(tmp_path):
    s = InternStore(db_path=str(tmp_path / "test.db"))
    yield s
    s.close()


@pytest.fixture
def registry(store):
    return InternRegistry(store)


def _make_jd(**overrides) -> InternJD:
    defaults = {
        "name": "Alex",
        "role": "Content Intern",
        "responsibilities": ["Write posts", "Track metrics"],
        "tone": "Professional",
        "tools_allowed": ["notion"],
        "autonomy_rules": "Always propose",
        "created_by": "U001",
    }
    defaults.update(overrides)
    return InternJD(**defaults)


# ---------------------------------------------------------------------------
# list_interns
# ---------------------------------------------------------------------------

def test_list_interns_empty(registry):
    assert registry.list_interns() == []


def test_list_interns_returns_active(registry):
    registry.create_intern(_make_jd(name="Alex"))
    registry.create_intern(_make_jd(name="Mia", role="Dev"))
    interns = registry.list_interns()
    assert len(interns) == 2
    names = {i.name for i in interns}
    assert names == {"Alex", "Mia"}


def test_list_interns_filters_inactive(registry):
    registry.create_intern(_make_jd(name="Alex"))
    registry.create_intern(_make_jd(name="Bob"))
    registry.deactivate_intern("Bob")
    interns = registry.list_interns()
    assert len(interns) == 1
    assert interns[0].name == "Alex"


def test_list_interns_preserves_fields(registry):
    registry.create_intern(_make_jd())
    intern = registry.list_interns()[0]
    assert intern.name == "Alex"
    assert intern.role == "Content Intern"
    assert intern.responsibilities == ["Write posts", "Track metrics"]
    assert intern.tone == "Professional"
    assert intern.tools_allowed == ["notion"]
    assert intern.autonomy_rules == "Always propose"
    assert intern.created_by == "U001"


# ---------------------------------------------------------------------------
# get_intern
# ---------------------------------------------------------------------------

def test_get_intern_case_insensitive(registry):
    registry.create_intern(_make_jd(name="Alex"))
    assert registry.get_intern("ALEX") is not None
    assert registry.get_intern("alex") is not None
    assert registry.get_intern("Alex") is not None


def test_get_intern_returns_none_for_unknown(registry):
    registry.create_intern(_make_jd(name="Alex"))
    assert registry.get_intern("Bob") is None


def test_get_intern_returns_none_for_inactive(registry):
    registry.create_intern(_make_jd(name="Alex"))
    registry.deactivate_intern("Alex")
    assert registry.get_intern("Alex") is None


# ---------------------------------------------------------------------------
# create_intern
# ---------------------------------------------------------------------------

def test_create_intern_success(registry):
    result = registry.create_intern(_make_jd(name="Mia"))
    assert result["ok"] is True


def test_create_intern_duplicate_name(registry):
    registry.create_intern(_make_jd(name="Alex"))
    result = registry.create_intern(_make_jd(name="Alex"))
    assert result["ok"] is False
    assert "already exists" in result["error"]


# ---------------------------------------------------------------------------
# update_intern
# ---------------------------------------------------------------------------

def test_update_intern_role(registry):
    registry.create_intern(_make_jd(name="Alex"))
    result = registry.update_intern("Alex", {"role": "Senior Content"})
    assert result["ok"] is True
    intern = registry.get_intern("Alex")
    assert intern.role == "Senior Content"


def test_update_intern_responsibilities(registry):
    registry.create_intern(_make_jd(name="Alex"))
    result = registry.update_intern("Alex", {"responsibilities": ["New task"]})
    assert result["ok"] is True
    intern = registry.get_intern("Alex")
    assert intern.responsibilities == ["New task"]


def test_update_intern_tools(registry):
    registry.create_intern(_make_jd(name="Alex"))
    result = registry.update_intern("Alex", {"tools_allowed": ["jira", "web_search"]})
    assert result["ok"] is True
    intern = registry.get_intern("Alex")
    assert set(intern.tools_allowed) == {"jira", "web_search"}


def test_update_intern_not_found(registry):
    result = registry.update_intern("Nobody", {"role": "X"})
    assert result["ok"] is False


def test_update_intern_no_fields(registry):
    registry.create_intern(_make_jd(name="Alex"))
    result = registry.update_intern("Alex", {})
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# deactivate_intern
# ---------------------------------------------------------------------------

def test_deactivate_intern(registry):
    registry.create_intern(_make_jd(name="Alex"))
    result = registry.deactivate_intern("Alex")
    assert result["ok"] is True
    assert registry.get_intern("Alex") is None


def test_deactivate_intern_not_found(registry):
    result = registry.deactivate_intern("Nobody")
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# get_intern_names
# ---------------------------------------------------------------------------

def test_get_intern_names(registry):
    registry.create_intern(_make_jd(name="Alex"))
    registry.create_intern(_make_jd(name="Mia", role="Dev"))
    names = registry.get_intern_names()
    assert "alex" in names
    assert "mia" in names


# ---------------------------------------------------------------------------
# Memory persistence
# ---------------------------------------------------------------------------

def test_save_memory(registry):
    registry.create_intern(_make_jd(name="Alex"))
    registry.save_memory("Alex", ["user asked about tasks", "mentioned deadline Friday"])
    intern = registry.get_intern("Alex")
    assert len(intern.memory) == 2
    assert "deadline Friday" in intern.memory[1]


def test_save_channel_memory(registry):
    registry.create_intern(_make_jd(name="Alex"))
    registry.save_memory("Alex", [], channel_memory={"C123": ["channel context"]})
    intern = registry.get_intern("Alex")
    assert "C123" in intern.channel_memory
    assert intern.channel_memory["C123"] == ["channel context"]


# ---------------------------------------------------------------------------
# InternStore direct tests
# ---------------------------------------------------------------------------

class TestInternStore:
    def test_create_and_get(self, store):
        result = store.create({"name": "Test", "role": "Dev"})
        assert result["ok"] is True
        row = store.get("Test")
        assert row is not None
        assert row["role"] == "Dev"

    def test_list_active(self, store):
        store.create({"name": "A", "role": "X"})
        store.create({"name": "B", "role": "Y"})
        store.deactivate("B")
        active = store.list_active()
        assert len(active) == 1
        assert active[0]["name"] == "A"

    def test_delete(self, store):
        store.create({"name": "A"})
        result = store.delete("A")
        assert result["ok"] is True
        assert store.get("A") is None

    def test_duplicate_create(self, store):
        store.create({"name": "A"})
        result = store.create({"name": "A"})
        assert result["ok"] is False
        assert "already exists" in result["error"]
