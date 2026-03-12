"""Tests for CrewAI custom tools — Notion read, web search, code exec."""
from unittest.mock import MagicMock, patch
import pytest

from src.tools.notion_read_tool import NotionReadTool
from src.tools.web_search_tool import WebSearchTool
from src.tools.code_exec_tool import CodeExecTool


# ---------------------------------------------------------------------------
# NotionReadTool
# ---------------------------------------------------------------------------

class TestNotionReadTool:
    def test_returns_context_on_success(self):
        mock_brain = MagicMock()
        mock_brain.get_context_for_request.return_value = "**Tasks**\n- Fix bug\n- Write docs"
        tool = NotionReadTool.create(mock_brain)
        result = tool._run("my tasks")
        assert "Fix bug" in result

    def test_returns_no_data_message(self):
        mock_brain = MagicMock()
        mock_brain.get_context_for_request.return_value = ""
        tool = NotionReadTool.create(mock_brain)
        result = tool._run("something obscure")
        assert "No matching" in result

    def test_returns_error_on_exception(self):
        mock_brain = MagicMock()
        mock_brain.get_context_for_request.side_effect = Exception("API error")
        tool = NotionReadTool.create(mock_brain)
        result = tool._run("tasks")
        assert "failed" in result.lower()

    def test_returns_not_connected_when_no_brain(self):
        tool = NotionReadTool()
        result = tool._run("tasks")
        assert "not connected" in result.lower()


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------

class TestWebSearchTool:
    @patch("src.tools.web_search_tool.DDGS")
    def test_returns_results(self, MockDDGS):
        mock_instance = MagicMock()
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_instance.text.return_value = [
            {"title": "Result 1", "body": "Snippet 1", "href": "https://example.com/1"},
            {"title": "Result 2", "body": "Snippet 2", "href": "https://example.com/2"},
        ]
        MockDDGS.return_value = mock_instance

        tool = WebSearchTool()
        result = tool._run("test query")
        assert "Result 1" in result
        assert "Result 2" in result

    @patch("src.tools.web_search_tool.DDGS")
    def test_returns_no_results_message(self, MockDDGS):
        mock_instance = MagicMock()
        mock_instance.__enter__ = MagicMock(return_value=mock_instance)
        mock_instance.__exit__ = MagicMock(return_value=False)
        mock_instance.text.return_value = []
        MockDDGS.return_value = mock_instance

        tool = WebSearchTool()
        result = tool._run("xyznoexist")
        assert "No results" in result


# ---------------------------------------------------------------------------
# CodeExecTool
# ---------------------------------------------------------------------------

class TestCodeExecTool:
    def test_runs_simple_code(self):
        tool = CodeExecTool()
        result = tool._run("print(2 + 2)")
        assert "4" in result

    def test_blocks_file_io(self):
        tool = CodeExecTool()
        result = tool._run("open('/etc/passwd')")
        assert "Blocked" in result

    def test_blocks_subprocess(self):
        tool = CodeExecTool()
        result = tool._run("import subprocess\nsubprocess.run(['ls'])")
        assert "Blocked" in result

    def test_blocks_eval(self):
        tool = CodeExecTool()
        result = tool._run("eval('1+1')")
        assert "Blocked" in result

    def test_handles_syntax_error(self):
        tool = CodeExecTool()
        result = tool._run("def oops(:\n  pass")
        assert "Error" in result or "SyntaxError" in result

    def test_returns_no_output(self):
        tool = CodeExecTool()
        result = tool._run("x = 42")
        assert result == "(no output)"
