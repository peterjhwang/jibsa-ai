"""Tests for Jibsa Doctor health check CLI."""
from unittest.mock import patch, MagicMock
import pytest

from src.doctor import check_config, check_env_vars, check_dependencies


def test_check_config_valid(tmp_path):
    """Valid config passes."""
    with patch("src.doctor._CONFIG_DIR", tmp_path):
        config_file = tmp_path / "settings.yaml"
        config_file.write_text(
            "llm:\n  provider: anthropic\n  model: claude-sonnet-4-20250514\n"
            "jibsa:\n  channel_name: jibsa\n"
        )
        assert check_config() is True


def test_check_config_missing_file(tmp_path):
    """Missing config file fails."""
    with patch("src.doctor._CONFIG_DIR", tmp_path):
        assert check_config() is False


def test_check_config_invalid_yaml(tmp_path):
    """Invalid YAML fails."""
    with patch("src.doctor._CONFIG_DIR", tmp_path):
        config_file = tmp_path / "settings.yaml"
        config_file.write_text("{{bad yaml:")
        assert check_config() is False


def test_check_env_vars_all_set():
    """All required env vars present passes."""
    env = {
        "SLACK_BOT_TOKEN": "xoxb-test-token-12345",
        "SLACK_APP_TOKEN": "xapp-test-token-12345",
        "ANTHROPIC_API_KEY": "sk-ant-test-key-12345",
    }
    with patch.dict("os.environ", env, clear=False), \
         patch("src.doctor._CONFIG_DIR", MagicMock()):
        # Mock the config file read
        import src.doctor as doc
        original = doc._CONFIG_DIR
        assert check_env_vars() is True


def test_check_env_vars_missing():
    """Missing required env vars fails."""
    with patch.dict("os.environ", {}, clear=True):
        assert check_env_vars() is False


def test_check_dependencies():
    """Required deps should be importable in test env."""
    assert check_dependencies() is True
