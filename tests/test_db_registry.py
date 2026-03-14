"""Tests for DatabaseRegistry."""
import json
import pytest
from pathlib import Path

from src.integrations.notion_db_registry import DatabaseRegistry


def test_register_and_get():
    reg = DatabaseRegistry()
    reg.register("Tasks", "abc-123", ["task", "todo"])
    assert reg.get_db_id("Tasks") == "abc-123"


def test_get_is_case_insensitive():
    reg = DatabaseRegistry()
    reg.register("Tasks", "abc-123")
    assert reg.get_db_id("tasks") == "abc-123"
    assert reg.get_db_id("TASKS") == "abc-123"


def test_get_returns_empty_when_not_found():
    reg = DatabaseRegistry()
    assert reg.get_db_id("Tasks") == ""


def test_register_overwrites():
    reg = DatabaseRegistry()
    reg.register("Tasks", "old-id")
    reg.register("Tasks", "new-id")
    assert reg.get_db_id("Tasks") == "new-id"


def test_get_matching_dbs():
    reg = DatabaseRegistry()
    reg.register("Tasks", "t1", ["task", "todo"])
    reg.register("Notes", "n1", ["note", "idea"])
    matches = reg.get_matching_dbs("what tasks do I have?")
    assert len(matches) == 1
    assert matches[0]["id"] == "t1"


def test_get_matching_dbs_returns_multiple():
    reg = DatabaseRegistry()
    reg.register("Tasks", "t1", ["task"])
    reg.register("Notes", "n1", ["note"])
    matches = reg.get_matching_dbs("take a note about this task")
    assert len(matches) == 2


def test_all_databases():
    reg = DatabaseRegistry()
    reg.register("Tasks", "t1")
    reg.register("Notes", "n1")
    assert len(reg.all_databases()) == 2


def test_from_yaml():
    databases = [
        {"name": "Tasks", "id": "t1", "keywords": ["task"]},
        {"name": "Notes", "id": "n1", "keywords": ["note"]},
    ]
    reg = DatabaseRegistry.from_yaml(databases)
    assert reg.get_db_id("Tasks") == "t1"
    assert reg.get_db_id("Notes") == "n1"


def test_from_yaml_skips_entries_without_name_or_id():
    databases = [
        {"name": "Tasks", "id": "t1"},
        {"name": "", "id": "n1"},
        {"name": "Projects", "id": ""},
    ]
    reg = DatabaseRegistry.from_yaml(databases)
    assert len(reg.all_databases()) == 1


def test_save_and_load_cache(tmp_path):
    cache_path = tmp_path / "cache.json"
    reg = DatabaseRegistry()
    reg.register("Tasks", "t1", ["task"])
    reg.register("Notes", "n1", ["note"])
    reg.save_cache(cache_path)

    reg2 = DatabaseRegistry()
    reg2.load_cache(cache_path)
    assert reg2.get_db_id("Tasks") == "t1"
    assert reg2.get_db_id("Notes") == "n1"


def test_load_cache_does_not_overwrite_existing(tmp_path):
    cache_path = tmp_path / "cache.json"
    reg = DatabaseRegistry()
    reg.register("Tasks", "cached-id", ["task"])
    reg.save_cache(cache_path)

    reg2 = DatabaseRegistry()
    reg2.register("Tasks", "yaml-id", ["task"])
    reg2.load_cache(cache_path)
    # YAML (pre-existing) takes precedence over cache
    assert reg2.get_db_id("Tasks") == "yaml-id"


def test_load_cache_nonexistent_path(tmp_path):
    reg = DatabaseRegistry()
    reg.load_cache(tmp_path / "nonexistent.json")
    assert reg.all_databases() == []


def test_load_cache_invalid_json(tmp_path):
    cache_path = tmp_path / "bad.json"
    cache_path.write_text("not json")
    reg = DatabaseRegistry()
    reg.load_cache(cache_path)
    assert reg.all_databases() == []
