"""Tests for Google Drive integration — client, tool, and orchestrator dispatch."""
import os
from unittest.mock import MagicMock, patch

import pytest

from src.context import current_user_id


class TestGoogleDriveClient:
    @pytest.fixture
    def client(self):
        with patch("src.integrations.google_drive_client.build") as mock_build:
            from src.integrations.google_drive_client import GoogleDriveClient
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            c = GoogleDriveClient(MagicMock())
            c._mock = mock_service
            return c

    def test_search_files(self, client):
        client._mock.files.return_value.list.return_value.execute.return_value = {
            "files": [
                {"id": "f1", "name": "Report.docx", "mimeType": "application/vnd.google-apps.document"},
                {"id": "f2", "name": "Budget.xlsx", "mimeType": "application/vnd.google-apps.spreadsheet"},
            ]
        }
        files = client.search_files("report")
        assert len(files) == 2
        assert files[0]["name"] == "Report.docx"

    def test_list_files_empty(self, client):
        client._mock.files.return_value.list.return_value.execute.return_value = {"files": []}
        files = client.list_files()
        assert files == []

    def test_get_file_metadata(self, client):
        client._mock.files.return_value.get.return_value.execute.return_value = {
            "id": "f1", "name": "Report", "mimeType": "text/plain",
        }
        meta = client.get_file_metadata("f1")
        assert meta["name"] == "Report"

    def test_execute_step_create_file(self, client):
        client._mock.files.return_value.create.return_value.execute.return_value = {
            "id": "f3", "name": "NewFile.txt", "webViewLink": "https://drive.google.com/file/f3",
        }
        step = {
            "action": "create_file",
            "params": {"name": "NewFile.txt", "content": "Hello world"},
            "description": "Create file",
        }
        result = client.execute_step(step)
        assert result["ok"] is True
        assert "f3" in result.get("url", "") or "f3" in result.get("file_id", "")

    def test_execute_step_unknown(self, client):
        step = {"action": "delete_file", "params": {}, "description": "Delete"}
        result = client.execute_step(step)
        assert result["ok"] is False


class TestDriveReadTool:
    def test_not_connected(self):
        from src.tools.drive_tool import DriveReadTool
        mock_oauth = MagicMock()
        mock_oauth.get_valid_credentials.return_value = None
        tool = DriveReadTool.create(mock_oauth)
        token = current_user_id.set("U001")
        result = tool._run("quarterly report")
        current_user_id.reset(token)
        assert "connect google" in result.lower()

    def test_returns_files(self):
        from src.tools.drive_tool import DriveReadTool
        mock_oauth = MagicMock()
        mock_oauth.get_valid_credentials.return_value = MagicMock()
        tool = DriveReadTool.create(mock_oauth)

        with patch("src.integrations.google_drive_client.build") as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service
            mock_service.files.return_value.list.return_value.execute.return_value = {
                "files": [{"name": "Q1 Report", "mimeType": "application/vnd.google-apps.document",
                           "modifiedTime": "2026-03-14T10:00:00Z", "webViewLink": "https://drive.google.com/f1"}]
            }
            token = current_user_id.set("U001")
            result = tool._run("Q1 report")
            current_user_id.reset(token)

        assert "Q1 Report" in result
        assert "Doc" in result


class TestDriveInCatalog:
    def test_drive_in_tool_catalog(self):
        from src.tool_registry import TOOL_CATALOG
        assert "drive" in TOOL_CATALOG
        assert "create_file" in TOOL_CATALOG["drive"]["write_actions"]

    def test_drive_in_valid_tool_names(self):
        from src.models.intern import VALID_TOOL_NAMES
        assert "drive" in VALID_TOOL_NAMES


class TestOrchestratorDriveDispatch:
    @pytest.fixture
    def orch(self, tmp_path):
        from src.orchestrator import Orchestrator
        config = {
            "jibsa": {
                "max_history": 20, "claude_timeout": 120, "timezone": "UTC",
                "intern_db_path": str(tmp_path / "interns.db"),
                "credential_db_path": str(tmp_path / "creds.db"),
            },
            "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            "approval": {"approve_keywords": ["yes"], "reject_keywords": ["no"]},
            "integrations": {},
        }
        env = {"SLACK_BOT_TOKEN": "xoxb-test", "ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict(os.environ, env), \
             patch("src.orchestrator.CrewRunner") as MockRunner, \
             patch("src.orchestrator.build_second_brain", return_value=None):
            MockRunner.return_value = MagicMock()
            o = Orchestrator(MagicMock(), config)
            return o

    def test_drive_step_no_user(self, orch):
        token = current_user_id.set("")
        result = orch._execute_google_step(
            {"action": "create_file", "params": {}, "description": "Create"},
            "drive",
        )
        current_user_id.reset(token)
        assert result["ok"] is False
