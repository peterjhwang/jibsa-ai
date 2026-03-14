"""Tests for the setup wizard helpers."""
from pathlib import Path

import pytest


class TestEnvHelpers:
    def test_read_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=value1\nKEY2=value2\n# comment\n\nKEY3=value3\n")

        import src.setup as setup
        original_env = setup._ENV_FILE
        setup._ENV_FILE = env_file
        try:
            result = setup._read_env()
            assert result == {"KEY1": "value1", "KEY2": "value2", "KEY3": "value3"}
        finally:
            setup._ENV_FILE = original_env

    def test_read_env_missing_file(self, tmp_path):
        import src.setup as setup
        original_env = setup._ENV_FILE
        setup._ENV_FILE = tmp_path / "nonexistent"
        try:
            result = setup._read_env()
            assert result == {}
        finally:
            setup._ENV_FILE = original_env

    def test_update_env_existing_key(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=old\nKEY2=keep\n")

        import src.setup as setup
        original_env = setup._ENV_FILE
        setup._ENV_FILE = env_file
        try:
            setup._update_env("KEY1", "new")
            content = env_file.read_text()
            assert "KEY1=new" in content
            assert "KEY2=keep" in content
            assert "KEY1=old" not in content
        finally:
            setup._ENV_FILE = original_env

    def test_update_env_new_key(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=value1\n")

        import src.setup as setup
        original_env = setup._ENV_FILE
        setup._ENV_FILE = env_file
        try:
            setup._update_env("NEW_KEY", "new_value")
            content = env_file.read_text()
            assert "KEY1=value1" in content
            assert "NEW_KEY=new_value" in content
        finally:
            setup._ENV_FILE = original_env

    def test_update_env_uncomments_key(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# NOTION_TOKEN=ntn_placeholder\n")

        import src.setup as setup
        original_env = setup._ENV_FILE
        setup._ENV_FILE = env_file
        try:
            setup._update_env("NOTION_TOKEN", "ntn_real_token")
            content = env_file.read_text()
            assert "NOTION_TOKEN=ntn_real_token" in content
            assert "# NOTION_TOKEN" not in content
        finally:
            setup._ENV_FILE = original_env

    def test_is_placeholder(self):
        from src.setup import _is_placeholder
        assert _is_placeholder("") is True
        assert _is_placeholder("xoxb-your-bot-token-here") is True
        assert _is_placeholder("your-key") is True
        assert _is_placeholder("sk-ant-your-key-here") is True
        assert _is_placeholder("xoxb-7234567890-real-token") is False
        assert _is_placeholder("ntn_1795ABC") is False
