"""Tests for CrewAI custom tools — Notion read, web search, code exec, slack, calendar, web reader, file gen."""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.tools.notion_read_tool import NotionReadTool
from src.tools.web_search_tool import WebSearchTool
from src.tools.code_exec_tool import CodeExecTool
from src.tools.slack_tool import SlackTool
from src.tools.calendar_tool import CalendarTool


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


# ---------------------------------------------------------------------------
# SlackTool
# ---------------------------------------------------------------------------

class TestSlackTool:
    def test_returns_action_plan_instructions(self):
        tool = SlackTool()
        result = tool._run(channel="#general", message="Hello team!")
        assert "action_plan" in result
        assert "#general" in result
        assert "Hello team!" in result

    def test_includes_post_message_action(self):
        tool = SlackTool()
        result = tool._run(channel="C0123ABC", message="Update")
        assert "post_message" in result
        assert "slack" in result

    def test_needs_approval_flag(self):
        tool = SlackTool()
        result = tool._run(channel="#dev", message="Deploy done")
        assert "needs_approval" in result


# ---------------------------------------------------------------------------
# CalendarTool
# ---------------------------------------------------------------------------

class TestCalendarTool:
    def test_read_query_returns_roadmap(self):
        tool = CalendarTool()
        result = tool._run(query="my meetings today")
        assert "Phase 3" in result or "coming" in result.lower()

    def test_write_query_returns_not_available(self):
        tool = CalendarTool()
        result = tool._run(query="schedule a call Thursday 2pm")
        assert "Phase 3" in result or "coming" in result.lower()

    def test_write_suggests_notion(self):
        tool = CalendarTool()
        result = tool._run(query="book a meeting with John")
        assert "Notion" in result

    def test_read_mentions_upcoming_features(self):
        tool = CalendarTool()
        result = tool._run(query="what's on my calendar")
        assert "event" in result.lower() or "meeting" in result.lower() or "schedule" in result.lower()


# ---------------------------------------------------------------------------
# WebReaderTool
# ---------------------------------------------------------------------------

class TestWebReaderTool:
    def test_returns_not_configured_without_api_key(self):
        from src.tools.web_reader_tool import WebReaderTool
        with patch.dict(os.environ, {}, clear=False):
            # Ensure ZENROWS_API_KEY is not set
            env = os.environ.copy()
            env.pop("ZENROWS_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                tool = WebReaderTool()
                result = tool._run("https://example.com")
                assert "not configured" in result.lower()

    @patch("src.tools.web_reader_tool.ZenRowsClient", create=True)
    def test_returns_content_on_success(self, MockClient):
        from src.tools.web_reader_tool import WebReaderTool
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>Hello World</p></body></html>"
        MockClient.return_value.get.return_value = mock_response

        with patch.dict(os.environ, {"ZENROWS_API_KEY": "test-key"}):
            tool = WebReaderTool()
            result = tool._run("https://example.com")
            assert "Hello World" in result

    @patch("src.tools.web_reader_tool.ZenRowsClient", create=True)
    def test_returns_error_on_http_failure(self, MockClient):
        from src.tools.web_reader_tool import WebReaderTool
        mock_response = MagicMock()
        mock_response.status_code = 404
        MockClient.return_value.get.return_value = mock_response

        with patch.dict(os.environ, {"ZENROWS_API_KEY": "test-key"}):
            tool = WebReaderTool()
            result = tool._run("https://example.com/404")
            assert "404" in result

    @patch("src.tools.web_reader_tool.ZenRowsClient", create=True)
    def test_handles_exception(self, MockClient):
        from src.tools.web_reader_tool import WebReaderTool
        MockClient.return_value.get.side_effect = Exception("Connection error")

        with patch.dict(os.environ, {"ZENROWS_API_KEY": "test-key"}):
            tool = WebReaderTool()
            result = tool._run("https://example.com")
            assert "Failed" in result

    def test_html_to_text_strips_tags(self):
        from src.tools.web_reader_tool import _html_to_text
        html = "<html><body><h1>Title</h1><p>Paragraph</p></body></html>"
        text = _html_to_text(html)
        assert "Title" in text
        assert "Paragraph" in text
        assert "<" not in text

    def test_html_to_text_strips_scripts(self):
        from src.tools.web_reader_tool import _html_to_text
        html = "<p>Before</p><script>alert('xss')</script><p>After</p>"
        text = _html_to_text(html)
        assert "Before" in text
        assert "After" in text
        assert "alert" not in text

    def test_prepends_https(self):
        from src.tools.web_reader_tool import WebReaderTool
        with patch.dict(os.environ, {"ZENROWS_API_KEY": "test-key"}):
            with patch("src.tools.web_reader_tool.ZenRowsClient", create=True) as MockClient:
                mock_response = MagicMock()
                mock_response.status_code = 200
                mock_response.text = "<p>Content</p>"
                MockClient.return_value.get.return_value = mock_response

                tool = WebReaderTool()
                tool._run("example.com")
                MockClient.return_value.get.assert_called_with("https://example.com")


# ---------------------------------------------------------------------------
# FileGenTool
# ---------------------------------------------------------------------------

class TestFileGenTool:
    def test_returns_action_plan_instructions(self):
        from src.tools.file_gen_tool import FileGenTool
        tool = FileGenTool()
        result = tool._run(filename="tasks.csv", content="name,status\nBug fix,Done")
        assert "action_plan" in result
        assert "file_gen" in result

    def test_rejects_unsupported_format(self):
        from src.tools.file_gen_tool import FileGenTool
        tool = FileGenTool()
        result = tool._run(filename="data.xlsx", content="data")
        assert "Unsupported" in result

    def test_create_and_get_path_csv(self):
        from src.tools.file_gen_tool import create_and_get_path
        path = create_and_get_path("test.csv", "a,b\n1,2")
        assert Path(path).exists()
        assert Path(path).read_text() == "a,b\n1,2"
        Path(path).unlink()

    def test_create_and_get_path_json_pretty(self):
        from src.tools.file_gen_tool import create_and_get_path
        path = create_and_get_path("data.json", '{"key":"value"}')
        content = Path(path).read_text()
        assert '"key": "value"' in content  # pretty-printed
        Path(path).unlink()

    def test_create_and_get_path_markdown(self):
        from src.tools.file_gen_tool import create_and_get_path
        path = create_and_get_path("report.md", "# Report\n\nContent here")
        assert Path(path).exists()
        assert "# Report" in Path(path).read_text()
        Path(path).unlink()


# ---------------------------------------------------------------------------
# ImageGenTool
# ---------------------------------------------------------------------------

class TestImageGenTool:
    def test_returns_not_configured_without_api_key(self):
        from src.tools.image_gen_tool import ImageGenTool
        env = os.environ.copy()
        env.pop("GOOGLE_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            tool = ImageGenTool()
            result = tool._run(prompt="a cat in space")
            assert "not configured" in result.lower()

    def test_returns_action_plan_instructions(self):
        from src.tools.image_gen_tool import ImageGenTool
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
            tool = ImageGenTool()
            result = tool._run(prompt="a sunset over mountains")
            assert "action_plan" in result
            assert "image_gen" in result
            assert "generate_image" in result

    def test_rejects_invalid_aspect_ratio(self):
        from src.tools.image_gen_tool import ImageGenTool
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
            tool = ImageGenTool()
            result = tool._run(prompt="a cat", aspect_ratio="500x500")
            assert "Invalid aspect ratio" in result

    def test_valid_aspect_ratios_accepted(self):
        from src.tools.image_gen_tool import ImageGenTool
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
            tool = ImageGenTool()
            for ratio in ["1:1", "9:16", "16:9", "3:2"]:
                result = tool._run(prompt="test", aspect_ratio=ratio)
                assert "action_plan" in result


# ---------------------------------------------------------------------------
# ReminderTool
# ---------------------------------------------------------------------------

class TestReminderTool:
    def test_returns_action_plan_instructions(self):
        from src.tools.reminder_tool import ReminderTool
        tool = ReminderTool()
        result = tool._run(message="Review PR", when="tomorrow at 9am")
        assert "action_plan" in result
        assert "reminder" in result
        assert "set_reminder" in result
