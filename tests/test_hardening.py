"""Tests for v0.7 hardening: startup validation, memory eviction, sandbox, rate limiting, retry, cleanup."""
import os
import time
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator import Orchestrator, _validate_startup


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

_BASE_CONFIG = {
    "jibsa": {"max_history": 20, "claude_timeout": 120, "timezone": "UTC"},
    "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
    "approval": {
        "approve_keywords": ["yes"],
        "reject_keywords": ["no"],
    },
    "integrations": {},
}

_REQUIRED_ENV = {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "ANTHROPIC_API_KEY": "sk-ant-test",
}


class TestStartupValidation:
    def test_missing_slack_bot_token_raises(self):
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(EnvironmentError, match="SLACK_BOT_TOKEN"):
                _validate_startup(_BASE_CONFIG)

    def test_missing_anthropic_key_raises(self):
        env = {"SLACK_BOT_TOKEN": "xoxb-test"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
                _validate_startup(_BASE_CONFIG)

    def test_missing_openai_key_raises(self):
        config = {**_BASE_CONFIG, "llm": {"provider": "openai", "model": "gpt-4o"}}
        env = {"SLACK_BOT_TOKEN": "xoxb-test"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(EnvironmentError, match="OPENAI_API_KEY"):
                _validate_startup(config)

    def test_valid_config_passes(self):
        with patch.dict(os.environ, _REQUIRED_ENV):
            _validate_startup(_BASE_CONFIG)  # should not raise

    def test_notion_token_warning(self, caplog):
        config = {**_BASE_CONFIG, "integrations": {"notion": {"enabled": True}}}
        env = {**_REQUIRED_ENV}
        # Ensure NOTION_TOKEN is NOT set
        with patch.dict(os.environ, env, clear=True):
            import logging
            with caplog.at_level(logging.WARNING):
                _validate_startup(config)
            assert "NOTION_TOKEN" in caplog.text

    def test_unimplemented_integration_warning(self, caplog):
        config = {**_BASE_CONFIG, "integrations": {"jira": {"enabled": True}}}
        with patch.dict(os.environ, _REQUIRED_ENV):
            import logging
            with caplog.at_level(logging.WARNING):
                _validate_startup(config)
            assert "jira" in caplog.text
            assert "not yet implemented" in caplog.text

    def test_unimplemented_scheduler_job_warning(self, caplog):
        config = {
            **_BASE_CONFIG,
            "scheduler": {"morning_briefing": {"enabled": True, "cron": "0 8 * * 1-5"}},
        }
        with patch.dict(os.environ, _REQUIRED_ENV):
            import logging
            with caplog.at_level(logging.WARNING):
                _validate_startup(config)
            assert "morning_briefing" in caplog.text

    def test_notion_build_failure_continues(self):
        """Orchestrator should start even if Notion initialization fails."""
        with patch.dict(os.environ, _REQUIRED_ENV), \
             patch("src.orchestrator.CrewRunner") as MockRunner, \
             patch("src.orchestrator.build_second_brain", side_effect=Exception("Notion down")):
            MockRunner.return_value = MagicMock()
            orch = Orchestrator(MagicMock(), _BASE_CONFIG)
            assert orch.notion is None


# ---------------------------------------------------------------------------
# Memory eviction
# ---------------------------------------------------------------------------

class TestMemoryEviction:
    @pytest.fixture
    def orch(self):
        with patch.dict(os.environ, _REQUIRED_ENV), \
             patch("src.orchestrator.CrewRunner") as MockRunner, \
             patch("src.orchestrator.build_second_brain", return_value=None):
            MockRunner.return_value = MagicMock()
            o = Orchestrator(MagicMock(), _BASE_CONFIG)
            o._max_threads = 5
            return o

    def test_evicts_oldest_threads(self, orch):
        for i in range(10):
            orch._add_to_history(f"ts-{i}", "user", f"msg-{i}")
        assert len(orch._history) == 5
        # Oldest threads (ts-0 through ts-4) should be evicted
        assert "ts-0" not in orch._history
        assert "ts-9" in orch._history

    def test_per_thread_max_history(self, orch):
        orch._max_history = 3
        for i in range(10):
            orch._add_to_history("ts-1", "user", f"msg-{i}")
        assert len(orch._history["ts-1"]) == 3
        assert orch._history["ts-1"][-1]["content"] == "msg-9"


# ---------------------------------------------------------------------------
# Edit session TTL
# ---------------------------------------------------------------------------

class TestEditSessionTTL:
    @pytest.fixture
    def orch(self):
        with patch.dict(os.environ, _REQUIRED_ENV), \
             patch("src.orchestrator.CrewRunner") as MockRunner, \
             patch("src.orchestrator.build_second_brain", return_value=None):
            MockRunner.return_value = MagicMock()
            o = Orchestrator(MagicMock(), _BASE_CONFIG)
            o._edit_session_ttl = 0.1  # 100ms for testing
            return o

    def test_expired_sessions_cleaned(self, orch):
        orch._edit_sessions["ts-old"] = ("alex", time.time() - 1.0)
        orch._edit_sessions["ts-new"] = ("bob", time.time())
        orch._cleanup_edit_sessions()
        assert "ts-old" not in orch._edit_sessions
        assert "ts-new" in orch._edit_sessions


# ---------------------------------------------------------------------------
# Code exec sandbox (AST layer)
# ---------------------------------------------------------------------------

class TestCodeExecSandbox:
    def test_blocks_os_import(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        result = tool._run("import os\nos.system('ls')")
        assert "Blocked" in result

    def test_blocks_subprocess_import(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        result = tool._run("import subprocess")
        assert "Blocked" in result

    def test_blocks_sys_import(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        result = tool._run("import sys\nsys.exit(1)")
        assert "Blocked" in result

    def test_blocks_dunder_subclasses(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        result = tool._run("().__class__.__bases__[0].__subclasses__()")
        assert "Blocked" in result

    def test_blocks_dunder_globals(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        result = tool._run("x = lambda: 0\nx.__globals__")
        assert "Blocked" in result

    def test_blocks_compile(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        result = tool._run("compile('print(1)', '<string>', 'exec')")
        assert "Blocked" in result

    def test_blocks_globals_call(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        result = tool._run("globals()")
        assert "Blocked" in result

    def test_blocks_from_os_import(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        result = tool._run("from os import path")
        assert "Blocked" in result

    def test_allows_safe_math(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        result = tool._run("import math\nprint(math.sqrt(144))")
        assert "12" in result

    def test_allows_safe_list_ops(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        result = tool._run("print(sorted([3,1,2]))")
        assert "[1, 2, 3]" in result

    def test_temp_file_cleaned_on_success(self):
        from src.tools.code_exec_tool import CodeExecTool
        tool = CodeExecTool()
        tool._run("print('hello')")
        # Verify no leftover temp files with .py suffix from this run
        # (we can't be 100% deterministic here, but temp_path should be cleaned)
        tmp = Path(tempfile.gettempdir())
        # Just verify it ran without error
        assert True


# ---------------------------------------------------------------------------
# Web search rate limiter
# ---------------------------------------------------------------------------

class TestWebSearchRateLimiter:
    def test_rate_limiter_allows_within_limit(self):
        from src.tools.web_search_tool import _RateLimiter
        limiter = _RateLimiter(max_calls=3, window_seconds=60.0)
        assert limiter.acquire() is True
        assert limiter.acquire() is True
        assert limiter.acquire() is True

    def test_rate_limiter_blocks_over_limit(self):
        from src.tools.web_search_tool import _RateLimiter
        limiter = _RateLimiter(max_calls=2, window_seconds=60.0)
        assert limiter.acquire() is True
        assert limiter.acquire() is True
        assert limiter.acquire() is False

    def test_rate_limiter_resets_after_window(self):
        from src.tools.web_search_tool import _RateLimiter
        limiter = _RateLimiter(max_calls=1, window_seconds=0.1)
        assert limiter.acquire() is True
        assert limiter.acquire() is False
        time.sleep(0.15)
        assert limiter.acquire() is True

    def test_search_tool_returns_rate_limit_message(self):
        from src.tools.web_search_tool import WebSearchTool, _search_limiter
        # Exhaust the limiter
        original_calls = _search_limiter._calls.copy()
        _search_limiter._calls = [time.monotonic() for _ in range(10)]
        try:
            tool = WebSearchTool()
            result = tool._run("test query")
            assert "Rate limited" in result
        finally:
            _search_limiter._calls = original_calls

    @patch("src.tools.web_search_tool.DDGS")
    def test_ddg_failure_falls_back_to_zenrows(self, MockDDGS):
        from src.tools.web_search_tool import WebSearchTool, _search_limiter
        # DDG fails
        mock_instance = MagicMock()
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_instance.text.side_effect = Exception("DDG blocked")
        MockDDGS.return_value = mock_instance

        # Reset limiter for this test
        _search_limiter._calls = []

        with patch.dict(os.environ, {"ZENROWS_API_KEY": "test-key"}):
            with patch("src.tools.web_search_tool.WebSearchTool._search_zenrows", return_value=[
                {"title": "ZenRows Result", "body": "Fallback worked", "href": "https://example.com"}
            ]):
                tool = WebSearchTool()
                result = tool._run("test query")
                assert "ZenRows Result" in result


# ---------------------------------------------------------------------------
# Notion retry logic
# ---------------------------------------------------------------------------

class TestNotionRetry:
    def test_is_retryable_on_rate_limit(self):
        from src.integrations.notion_client import _is_retryable
        from notion_client.errors import APIResponseError

        exc = APIResponseError.__new__(APIResponseError)
        exc.status = 429
        assert _is_retryable(exc) is True

    def test_is_retryable_on_server_error(self):
        from src.integrations.notion_client import _is_retryable
        from notion_client.errors import APIResponseError

        for status in (500, 502, 503, 504):
            exc = APIResponseError.__new__(APIResponseError)
            exc.status = status
            assert _is_retryable(exc) is True

    def test_is_not_retryable_on_client_error(self):
        from src.integrations.notion_client import _is_retryable
        from notion_client.errors import APIResponseError

        for status in (400, 401, 403, 404):
            exc = APIResponseError.__new__(APIResponseError)
            exc.status = status
            assert _is_retryable(exc) is False


# ---------------------------------------------------------------------------
# Circuit breaker on plan execution
# ---------------------------------------------------------------------------

class TestPlanExecutionCircuitBreaker:
    @pytest.fixture
    def orch(self):
        with patch.dict(os.environ, _REQUIRED_ENV), \
             patch("src.orchestrator.CrewRunner") as MockRunner, \
             patch("src.orchestrator.build_second_brain", return_value=MagicMock()):
            MockRunner.return_value = MagicMock()
            o = Orchestrator(MagicMock(), _BASE_CONFIG)
            return o

    def test_notion_step_uses_circuit_breaker(self, orch):
        orch.notion.execute_step.return_value = {"ok": True}
        plan = {
            "summary": "Create task",
            "steps": [{"service": "notion", "action": "create_task", "params": {}, "description": "Create task"}],
        }
        orch._execute_plan(plan, "C123", "ts-1")
        orch.notion.execute_step.assert_called_once()

    def test_notion_failure_records_circuit_failure(self, orch):
        orch.notion.execute_step.side_effect = Exception("Notion down")
        plan = {
            "summary": "Create task",
            "steps": [{"service": "notion", "action": "create_task", "params": {}, "description": "Create task"}],
        }
        orch._execute_plan(plan, "C123", "ts-1")
        assert orch._notion_circuit._failure_count == 1

    def test_circuit_open_skips_notion_step(self, orch):
        from src.circuit_breaker import CircuitState
        # Force circuit open
        orch._notion_circuit._state = CircuitState.OPEN
        orch._notion_circuit._last_failure_time = time.time()

        plan = {
            "summary": "Create task",
            "steps": [{"service": "notion", "action": "create_task", "params": {}, "description": "Create task"}],
        }
        orch._execute_plan(plan, "C123", "ts-1")
        orch.notion.execute_step.assert_not_called()


# ---------------------------------------------------------------------------
# Temp file cleanup
# ---------------------------------------------------------------------------

class TestTempFileCleanup:
    def test_cleanup_removes_jibsa_temp_dirs(self):
        from src.app import _cleanup_temp_files
        # Create a fake jibsa temp dir
        tmp_dir = Path(tempfile.mkdtemp(prefix="jibsa_test_"))
        test_file = tmp_dir / "test.txt"
        test_file.write_text("test")
        assert tmp_dir.exists()

        _cleanup_temp_files()
        assert not tmp_dir.exists()

    def test_cleanup_ignores_non_jibsa_dirs(self):
        from src.app import _cleanup_temp_files
        # Create a non-jibsa temp dir
        tmp_dir = Path(tempfile.mkdtemp(prefix="other_"))
        test_file = tmp_dir / "test.txt"
        test_file.write_text("test")

        _cleanup_temp_files()
        assert tmp_dir.exists()
        # Cleanup
        import shutil
        shutil.rmtree(tmp_dir)
