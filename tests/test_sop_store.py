"""Tests for SOPStore — SQLite CRUD for SOPs."""
import pytest

from src.integrations.sop_store import SOPStore


@pytest.fixture
def store(tmp_path):
    s = SOPStore(db_path=str(tmp_path / "test.db"))
    yield s
    s.close()


def _make_sop(**overrides) -> dict:
    defaults = {
        "name": "weekly-report",
        "intern_id": None,
        "trigger_keywords": ["weekly", "report", "summary"],
        "description": "Generate a weekly summary report.",
        "steps": ["Query tasks", "Summarize", "Format"],
        "expected_output": "A formatted weekly report.",
        "tools_required": ["notion"],
        "approval_required": False,
        "priority": 10,
    }
    defaults.update(overrides)
    return defaults


class TestCreate:
    def test_create_basic(self, store):
        result = store.create(_make_sop())
        assert result["ok"] is True
        assert "id" in result

    def test_create_returns_id(self, store):
        result = store.create(_make_sop())
        sop = store.get(result["id"])
        assert sop is not None
        assert sop["name"] == "weekly-report"

    def test_create_missing_name(self, store):
        result = store.create(_make_sop(name=""))
        assert result["ok"] is False
        assert "required" in result["error"].lower()

    def test_create_duplicate_name_same_scope(self, store):
        store.create(_make_sop(name="dup"))
        result = store.create(_make_sop(name="dup"))
        assert result["ok"] is False
        assert "already exists" in result["error"]

    def test_create_same_name_different_intern(self, store):
        r1 = store.create(_make_sop(name="report", intern_id="alex"))
        r2 = store.create(_make_sop(name="report", intern_id="mia"))
        assert r1["ok"] is True
        assert r2["ok"] is True

    def test_create_shared_and_intern_same_name(self, store):
        r1 = store.create(_make_sop(name="report", intern_id=None))
        r2 = store.create(_make_sop(name="report", intern_id="alex"))
        assert r1["ok"] is True
        assert r2["ok"] is True

    def test_create_custom_id(self, store):
        result = store.create(_make_sop(id="custom-id-123"))
        assert result["ok"] is True
        assert result["id"] == "custom-id-123"


class TestGet:
    def test_get_by_id(self, store):
        result = store.create(_make_sop())
        sop = store.get(result["id"])
        assert sop["name"] == "weekly-report"
        assert sop["priority"] == 10
        assert sop["approval_required"] is False

    def test_get_nonexistent(self, store):
        assert store.get("nonexistent") is None

    def test_get_by_name_shared(self, store):
        store.create(_make_sop(name="my-sop"))
        sop = store.get_by_name("my-sop")
        assert sop is not None
        assert sop["name"] == "my-sop"

    def test_get_by_name_case_insensitive(self, store):
        store.create(_make_sop(name="my-sop"))
        sop = store.get_by_name("MY-SOP")
        assert sop is not None

    def test_get_by_name_intern_scoped(self, store):
        store.create(_make_sop(name="my-sop", intern_id="alex"))
        assert store.get_by_name("my-sop", intern_id="alex") is not None
        assert store.get_by_name("my-sop") is None  # shared scope

    def test_get_by_name_nonexistent(self, store):
        assert store.get_by_name("nope") is None


class TestList:
    def test_list_for_intern_includes_shared(self, store):
        store.create(_make_sop(name="shared-sop", intern_id=None))
        store.create(_make_sop(name="alex-sop", intern_id="alex"))
        store.create(_make_sop(name="mia-sop", intern_id="mia"))

        alex_sops = store.list_for_intern("alex")
        names = {s["name"] for s in alex_sops}
        assert names == {"shared-sop", "alex-sop"}

    def test_list_shared_only(self, store):
        store.create(_make_sop(name="shared", intern_id=None))
        store.create(_make_sop(name="intern", intern_id="alex"))

        shared = store.list_shared()
        assert len(shared) == 1
        assert shared[0]["name"] == "shared"

    def test_list_all(self, store):
        store.create(_make_sop(name="a"))
        store.create(_make_sop(name="b", intern_id="alex"))
        store.create(_make_sop(name="c", intern_id="mia"))
        assert len(store.list_all()) == 3

    def test_list_empty(self, store):
        assert store.list_for_intern("alex") == []
        assert store.list_shared() == []
        assert store.list_all() == []

    def test_list_ordered_by_priority(self, store):
        store.create(_make_sop(name="low", priority=1))
        store.create(_make_sop(name="high", priority=100))
        store.create(_make_sop(name="mid", priority=50))

        sops = store.list_all()
        names = [s["name"] for s in sops]
        assert names == ["high", "mid", "low"]


class TestUpdate:
    def test_update_description(self, store):
        result = store.create(_make_sop())
        store.update(result["id"], {"description": "Updated!"})
        sop = store.get(result["id"])
        assert sop["description"] == "Updated!"

    def test_update_json_fields(self, store):
        result = store.create(_make_sop())
        store.update(result["id"], {
            "trigger_keywords": ["new", "keywords"],
            "steps": ["step1", "step2"],
            "tools_required": ["jira"],
        })
        sop = store.get(result["id"])
        assert sop["trigger_keywords"] == ["new", "keywords"]
        assert sop["steps"] == ["step1", "step2"]
        assert sop["tools_required"] == ["jira"]

    def test_update_approval_required(self, store):
        result = store.create(_make_sop(approval_required=False))
        store.update(result["id"], {"approval_required": True})
        sop = store.get(result["id"])
        assert sop["approval_required"] is True

    def test_update_priority(self, store):
        result = store.create(_make_sop(priority=0))
        store.update(result["id"], {"priority": 99})
        sop = store.get(result["id"])
        assert sop["priority"] == 99

    def test_update_nonexistent(self, store):
        result = store.update("nonexistent", {"description": "x"})
        assert result["ok"] is False

    def test_update_no_fields(self, store):
        result = store.create(_make_sop())
        result = store.update(result["id"], {})
        assert result["ok"] is False


class TestDelete:
    def test_delete(self, store):
        result = store.create(_make_sop())
        del_result = store.delete(result["id"])
        assert del_result["ok"] is True
        assert store.get(result["id"]) is None

    def test_delete_nonexistent(self, store):
        result = store.delete("nonexistent")
        assert result["ok"] is False


class TestJsonSerialization:
    def test_roundtrip(self, store):
        data = _make_sop(
            trigger_keywords=["a", "b", "c"],
            steps=["step 1", "step 2"],
            tools_required=["notion", "jira"],
        )
        result = store.create(data)
        sop = store.get(result["id"])
        assert sop["trigger_keywords"] == ["a", "b", "c"]
        assert sop["steps"] == ["step 1", "step 2"]
        assert sop["tools_required"] == ["notion", "jira"]
        assert isinstance(sop["trigger_keywords"], list)

    def test_empty_lists(self, store):
        data = _make_sop(trigger_keywords=[], steps=[], tools_required=[])
        result = store.create(data)
        sop = store.get(result["id"])
        assert sop["trigger_keywords"] == []
        assert sop["steps"] == []
        assert sop["tools_required"] == []
