"""Tests for CredentialStore — encrypted per-user SQLite credential storage."""
import os
import logging
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet


class TestCredentialStore:
    @pytest.fixture
    def store(self, tmp_path):
        key = Fernet.generate_key().decode()
        from src.integrations.credential_store import CredentialStore
        s = CredentialStore(db_path=str(tmp_path / "test.db"), encryption_key=key)
        yield s
        s.close()

    def test_set_and_get(self, store):
        store.set("U001", "google", {"access_token": "abc", "refresh_token": "xyz"})
        data = store.get("U001", "google")
        assert data is not None
        assert data["access_token"] == "abc"
        assert data["refresh_token"] == "xyz"

    def test_get_nonexistent(self, store):
        assert store.get("U999", "google") is None

    def test_overwrite(self, store):
        store.set("U001", "google", {"access_token": "old"})
        store.set("U001", "google", {"access_token": "new"})
        data = store.get("U001", "google")
        assert data["access_token"] == "new"

    def test_delete(self, store):
        store.set("U001", "google", {"access_token": "abc"})
        assert store.delete("U001", "google") is True
        assert store.get("U001", "google") is None

    def test_delete_nonexistent(self, store):
        assert store.delete("U999", "google") is False

    def test_list_services(self, store):
        store.set("U001", "google", {"token": "a"})
        store.set("U001", "github", {"token": "b"})
        store.set("U002", "google", {"token": "c"})

        services = store.list_services("U001")
        assert sorted(services) == ["github", "google"]
        assert store.list_services("U002") == ["google"]
        assert store.list_services("U999") == []

    def test_user_isolation(self, store):
        store.set("U001", "google", {"token": "user1"})
        store.set("U002", "google", {"token": "user2"})
        assert store.get("U001", "google")["token"] == "user1"
        assert store.get("U002", "google")["token"] == "user2"

    def test_wrong_key_returns_none(self, tmp_path):
        from src.integrations.credential_store import CredentialStore
        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()

        db_path = str(tmp_path / "test.db")
        store1 = CredentialStore(db_path=db_path, encryption_key=key1)
        store1.set("U001", "google", {"secret": "data"})
        store1.close()

        store2 = CredentialStore(db_path=db_path, encryption_key=key2)
        assert store2.get("U001", "google") is None
        store2.close()

    def test_auto_generated_key_warning(self, tmp_path, caplog):
        from src.integrations.credential_store import CredentialStore
        with patch.dict(os.environ, {}, clear=False):
            env = os.environ.copy()
            env.pop("CREDENTIAL_ENCRYPTION_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with caplog.at_level(logging.WARNING):
                    s = CredentialStore(db_path=str(tmp_path / "test.db"))
                    assert "CREDENTIAL_ENCRYPTION_KEY" in caplog.text
                    s.close()

    def test_expires_at_stored(self, store):
        store.set("U001", "google", {"access_token": "abc", "expiry": "2025-12-31T23:59:59"})
        # Verify it was stored (read back from DB directly)
        row = store._conn.execute(
            "SELECT expires_at FROM credentials WHERE slack_user_id = ? AND service = ?",
            ("U001", "google"),
        ).fetchone()
        assert row["expires_at"] == "2025-12-31T23:59:59"


class TestGoogleOAuth:
    @pytest.fixture
    def store(self, tmp_path):
        key = Fernet.generate_key().decode()
        from src.integrations.credential_store import CredentialStore
        s = CredentialStore(db_path=str(tmp_path / "test.db"), encryption_key=key)
        yield s
        s.close()

    def test_not_configured(self, store):
        from src.integrations.google_oauth import GoogleOAuthManager
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}, clear=False):
            mgr = GoogleOAuthManager(store)
            assert mgr.is_configured is False
            assert mgr.generate_auth_url() is None

    def test_configured(self, store):
        from src.integrations.google_oauth import GoogleOAuthManager
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "test-id", "GOOGLE_CLIENT_SECRET": "test-secret"}):
            mgr = GoogleOAuthManager(store)
            assert mgr.is_configured is True

    def test_generate_auth_url(self, store):
        from src.integrations.google_oauth import GoogleOAuthManager
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "test-id", "GOOGLE_CLIENT_SECRET": "test-secret"}):
            mgr = GoogleOAuthManager(store)
            url = mgr.generate_auth_url()
            assert url is not None
            assert "accounts.google.com" in url
            assert "test-id" in url

    def test_exchange_code_not_configured(self, store):
        from src.integrations.google_oauth import GoogleOAuthManager
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}):
            mgr = GoogleOAuthManager(store)
            result = mgr.exchange_code("U001", "fake-code")
            assert result["ok"] is False
            assert "not configured" in result["error"]

    @patch("src.integrations.google_oauth.Flow")
    def test_exchange_code_success(self, MockFlow, store):
        from src.integrations.google_oauth import GoogleOAuthManager
        mock_flow = MockFlow.from_client_config.return_value
        mock_creds = mock_flow.credentials
        mock_creds.token = "access-token"
        mock_creds.refresh_token = "refresh-token"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "test-id"
        mock_creds.client_secret = "test-secret"
        mock_creds.scopes = ["calendar.readonly"]
        mock_creds.expiry = None

        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "test-id", "GOOGLE_CLIENT_SECRET": "test-secret"}):
            mgr = GoogleOAuthManager(store)
            result = mgr.exchange_code("U001", "valid-code")
            assert result["ok"] is True
            # Verify tokens were stored
            data = store.get("U001", "google")
            assert data is not None
            assert data["access_token"] == "access-token"

    def test_revoke_no_credentials(self, store):
        from src.integrations.google_oauth import GoogleOAuthManager
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "secret"}):
            mgr = GoogleOAuthManager(store)
            result = mgr.revoke_and_delete("U999")
            assert result["ok"] is False

    @patch("requests.post")
    def test_revoke_and_delete(self, mock_post, store):
        from src.integrations.google_oauth import GoogleOAuthManager
        mock_post.return_value.status_code = 200

        store.set("U001", "google", {"access_token": "abc", "refresh_token": "xyz"})
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "secret"}):
            mgr = GoogleOAuthManager(store)
            result = mgr.revoke_and_delete("U001")
            assert result["ok"] is True
            assert store.get("U001", "google") is None

    def test_get_valid_credentials_no_data(self, store):
        from src.integrations.google_oauth import GoogleOAuthManager
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "secret"}):
            mgr = GoogleOAuthManager(store)
            assert mgr.get_valid_credentials("U999") is None


class TestOrchestratorConnections:
    @pytest.fixture
    def orch(self, tmp_path):
        from src.orchestrator import Orchestrator
        config = {
            "jibsa": {"max_history": 20, "claude_timeout": 120, "timezone": "UTC", "credential_db_path": str(tmp_path / "creds.db")},
            "llm": {"provider": "anthropic", "model": "claude-sonnet-4-20250514"},
            "approval": {"approve_keywords": ["yes"], "reject_keywords": ["no"]},
            "integrations": {},
        }
        env = {"SLACK_BOT_TOKEN": "xoxb-test", "ANTHROPIC_API_KEY": "sk-ant-test"}
        from unittest.mock import MagicMock
        with patch.dict(os.environ, env), \
             patch("src.orchestrator.CrewRunner") as MockRunner, \
             patch("src.orchestrator.build_second_brain", return_value=None):
            MockRunner.return_value = MagicMock()
            mock_slack = MagicMock()
            mock_slack.chat_postMessage.return_value = {"ts": "t1"}
            o = Orchestrator(mock_slack, config)
            o.runner = MockRunner.return_value
            yield o
            o.credential_store.close()

    def test_list_connections_empty(self, orch):
        orch.handle_message("C123", "ts-1", "U001", "my connections")
        text = orch.slack.chat_postMessage.call_args.kwargs["text"]
        assert "no connected" in text.lower()

    def test_connect_unknown_service(self, orch):
        orch.handle_message("C123", "ts-2", "U001", "connect twitter")
        text = orch.slack.chat_postMessage.call_args.kwargs["text"]
        assert "Unknown service" in text

    def test_connect_google_not_configured(self, orch):
        with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}):
            orch.google_oauth._client_id = ""
            orch.google_oauth._client_secret = ""
            orch.handle_message("C123", "ts-3", "U001", "connect google")
            text = orch.slack.chat_postMessage.call_args.kwargs["text"]
            assert "not configured" in text.lower()

    def test_disconnect_not_connected(self, orch):
        orch.handle_message("C123", "ts-4", "U001", "disconnect google")
        text = orch.slack.chat_postMessage.call_args.kwargs["text"]
        assert "No Google credentials" in text or "not disconnect" in text.lower()


class TestRouterConnections:
    def test_connect_routes_to_orchestrator(self):
        from src.router import MessageRouter
        router = MessageRouter(["alex"])
        result = router.route("connect google")
        assert result.intern_name is None
        assert result.message == "connect google"

    def test_disconnect_routes_to_orchestrator(self):
        from src.router import MessageRouter
        router = MessageRouter(["alex"])
        result = router.route("disconnect google")
        assert result.intern_name is None

    def test_my_connections_routes_to_orchestrator(self):
        from src.router import MessageRouter
        router = MessageRouter(["alex"])
        result = router.route("my connections")
        assert result.intern_name is None
        assert result.message == "my connections"


class TestContextVar:
    def test_context_var_set_and_read(self):
        from src.context import current_user_id
        token = current_user_id.set("U123")
        assert current_user_id.get() == "U123"
        current_user_id.reset(token)
        assert current_user_id.get() == ""
