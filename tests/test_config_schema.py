"""Tests for config validation."""
import pytest
from pydantic import ValidationError

from src.config_schema import Settings, validate_config


def test_default_config_is_valid():
    """Empty dict should produce valid defaults."""
    settings = validate_config({})
    assert settings.llm.provider == "anthropic"
    assert settings.jibsa.max_history == 20
    assert settings.approval.ttl_seconds == 3600


def test_full_config_is_valid():
    """A complete config matching settings.yaml should validate."""
    raw = {
        "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514", "temperature": 0.7, "max_tokens": 4096},
        "jibsa": {
            "channel_name": "jibsa",
            "timezone": "Asia/Seoul",
            "max_history": 20,
            "claude_timeout": 120,
            "claude_max_concurrent": 3,
            "crew_timeout": 300,
            "crew_max_iter": 10,
            "code_exec_timeout": 30,
            "code_exec_max_output": 4000,
        },
        "approval": {
            "approve_keywords": ["yes"],
            "reject_keywords": ["no"],
            "ttl_seconds": 3600,
        },
        "integrations": {
            "notion": {"enabled": True},
            "jira": {"enabled": False},
        },
    }
    settings = validate_config(raw)
    assert settings.llm.provider == "anthropic"
    assert settings.jibsa.crew_timeout == 300
    assert settings.approval.ttl_seconds == 3600
    assert settings.integrations.notion.enabled is True


def test_invalid_temperature_rejected():
    with pytest.raises(ValidationError, match="temperature"):
        validate_config({"llm": {"temperature": 5.0}})


def test_negative_max_history_rejected():
    with pytest.raises(ValidationError, match="max_history"):
        validate_config({"jibsa": {"max_history": 0}})


def test_empty_approve_keywords_rejected():
    with pytest.raises(ValidationError, match="keyword"):
        validate_config({"approval": {"approve_keywords": []}})


def test_crew_timeout_too_low_rejected():
    with pytest.raises(ValidationError, match="crew_timeout"):
        validate_config({"jibsa": {"crew_timeout": 5}})


def test_extra_fields_are_ignored():
    """Unknown fields should not cause validation errors."""
    settings = validate_config({"jibsa": {"channel_name": "test", "unknown_field": 42}})
    assert settings.jibsa.channel_name == "test"
