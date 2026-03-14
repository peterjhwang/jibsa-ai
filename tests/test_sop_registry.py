"""Tests for SOPRegistry — resolution, CRUD, YAML seeding."""
import os
import pytest

from src.integrations.sop_store import SOPStore
from src.models.sop import SOP
from src.sop_registry import SOPRegistry


@pytest.fixture
def store(tmp_path):
    s = SOPStore(db_path=str(tmp_path / "test.db"))
    yield s
    s.close()


@pytest.fixture
def registry(store):
    return SOPRegistry(store)


def _make_sop(**overrides) -> SOP:
    defaults = {
        "name": "weekly-report",
        "trigger_keywords": ["weekly", "report", "summary"],
        "description": "Generate a weekly summary.",
        "steps": ["Query tasks", "Summarize"],
        "expected_output": "A formatted report.",
        "tools_required": ["notion"],
        "approval_required": False,
        "priority": 10,
    }
    defaults.update(overrides)
    return SOP(**defaults)


class TestCRUD:
    def test_create_and_get(self, registry):
        result = registry.create_sop(_make_sop())
        assert result["ok"] is True
        sop = registry.get_sop(result["id"])
        assert sop is not None
        assert sop.name == "weekly-report"

    def test_create_invalid(self, registry):
        result = registry.create_sop(_make_sop(name=""))
        assert result["ok"] is False

    def test_get_by_name(self, registry):
        registry.create_sop(_make_sop())
        sop = registry.get_sop_by_name("weekly-report")
        assert sop is not None

    def test_list_for_intern(self, registry):
        registry.create_sop(_make_sop(name="shared", intern_id=None))
        registry.create_sop(_make_sop(name="alex-only", intern_id="alex"))
        sops = registry.list_sops_for_intern("alex")
        names = {s.name for s in sops}
        assert names == {"shared", "alex-only"}

    def test_list_all(self, registry):
        registry.create_sop(_make_sop(name="a"))
        registry.create_sop(_make_sop(name="b", intern_id="alex"))
        assert len(registry.list_all_sops()) == 2

    def test_update(self, registry):
        result = registry.create_sop(_make_sop())
        registry.update_sop(result["id"], {"description": "Updated"})
        sop = registry.get_sop(result["id"])
        assert sop.description == "Updated"

    def test_delete(self, registry):
        result = registry.create_sop(_make_sop())
        registry.delete_sop(result["id"])
        assert registry.get_sop(result["id"]) is None


class TestResolveSops:
    def test_keyword_match(self, registry):
        registry.create_sop(_make_sop(
            name="weekly-report",
            trigger_keywords=["weekly", "report", "summary"],
        ))
        sop = registry.resolve_sops("alex", "can you give me the weekly report?")
        assert sop is not None
        assert sop.name == "weekly-report"

    def test_no_match_returns_none(self, registry):
        registry.create_sop(_make_sop(
            name="weekly-report",
            trigger_keywords=["weekly", "report"],
        ))
        sop = registry.resolve_sops("alex", "what is the weather today?")
        assert sop is None

    def test_priority_tiebreak(self, registry):
        registry.create_sop(_make_sop(
            name="low-priority",
            trigger_keywords=["task"],
            priority=1,
        ))
        registry.create_sop(_make_sop(
            name="high-priority",
            trigger_keywords=["task"],
            priority=50,
        ))
        sop = registry.resolve_sops("alex", "create a task")
        assert sop.name == "high-priority"

    def test_score_tiebreak(self, registry):
        registry.create_sop(_make_sop(
            name="partial-match",
            trigger_keywords=["weekly"],
            priority=10,
        ))
        registry.create_sop(_make_sop(
            name="full-match",
            trigger_keywords=["weekly", "report"],
            priority=10,
        ))
        sop = registry.resolve_sops("alex", "weekly report please")
        assert sop.name == "full-match"

    def test_name_tiebreak_deterministic(self, registry):
        """When priority and score are equal, sort by name ASC."""
        registry.create_sop(_make_sop(
            name="b-sop",
            trigger_keywords=["task"],
            priority=10,
        ))
        registry.create_sop(_make_sop(
            name="a-sop",
            trigger_keywords=["task"],
            priority=10,
        ))
        sop = registry.resolve_sops("alex", "task")
        assert sop.name == "a-sop"

    def test_case_insensitive_keywords(self, registry):
        registry.create_sop(_make_sop(
            name="report-sop",
            trigger_keywords=["WEEKLY", "Report"],
        ))
        sop = registry.resolve_sops("alex", "weekly report")
        assert sop is not None

    def test_shared_sops_included(self, registry):
        registry.create_sop(_make_sop(
            name="shared-sop",
            intern_id=None,
            trigger_keywords=["shared"],
        ))
        sop = registry.resolve_sops("any-intern", "shared task")
        assert sop is not None
        assert sop.name == "shared-sop"

    def test_intern_specific_over_shared(self, registry):
        """Intern-specific SOP wins over shared when priority is higher."""
        registry.create_sop(_make_sop(
            name="shared-sop",
            intern_id=None,
            trigger_keywords=["report"],
            priority=5,
        ))
        registry.create_sop(_make_sop(
            name="alex-sop",
            intern_id="alex",
            trigger_keywords=["report"],
            priority=20,
        ))
        sop = registry.resolve_sops("alex", "generate report")
        assert sop.name == "alex-sop"

    def test_empty_store_returns_none(self, registry):
        assert registry.resolve_sops("alex", "anything") is None


class TestSeedFromYaml:
    def test_seed(self, store, tmp_path):
        yaml_content = """
sops:
  - name: test-sop
    intern_id: null
    trigger_keywords: [test]
    description: A test SOP.
    steps: [step one]
    expected_output: output
    tools_required: []
    approval_required: false
    priority: 5
"""
        yaml_path = tmp_path / "sops.yaml"
        yaml_path.write_text(yaml_content)

        count = SOPRegistry.seed_from_yaml(store, str(yaml_path))
        assert count == 1
        assert store.get_by_name("test-sop") is not None

    def test_seed_skips_existing(self, store, tmp_path):
        store.create({
            "name": "existing",
            "trigger_keywords": ["x"],
            "description": "Original",
            "steps": ["s"],
            "expected_output": "o",
        })

        yaml_content = """
sops:
  - name: existing
    trigger_keywords: [x]
    description: Updated
    steps: [s]
    expected_output: o
"""
        yaml_path = tmp_path / "sops.yaml"
        yaml_path.write_text(yaml_content)

        count = SOPRegistry.seed_from_yaml(store, str(yaml_path))
        assert count == 0
        # Original should be preserved
        sop = store.get_by_name("existing")
        assert sop["description"] == "Original"

    def test_seed_multiple(self, store, tmp_path):
        yaml_content = """
sops:
  - name: sop-a
    trigger_keywords: [a]
    description: A
    steps: [s]
    expected_output: o
  - name: sop-b
    trigger_keywords: [b]
    description: B
    steps: [s]
    expected_output: o
"""
        yaml_path = tmp_path / "sops.yaml"
        yaml_path.write_text(yaml_content)

        count = SOPRegistry.seed_from_yaml(store, str(yaml_path))
        assert count == 2
