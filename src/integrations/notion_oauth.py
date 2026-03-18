"""
NotionOAuthManager — handles per-user Notion OAuth credentials.

Desktop app OAuth flow (localhost redirect):
  1. Bot generates an auth URL and DMs it to the user
  2. User clicks the link and selects pages to share
  3. Notion redirects to http://localhost (nothing listening — page won't load)
  4. User copies the "code" value from the URL bar and pastes it back
  5. Bot exchanges the code for a token and stores it encrypted

Notion tokens don't expire — no refresh flow needed.
Tokens are stored via CredentialStore (encrypted SQLite).
"""
import base64
import logging
import os
import secrets
from urllib.parse import urlencode

import requests as _requests

logger = logging.getLogger(__name__)

SERVICE_NAME = "notion"

_AUTH_URL = "https://api.notion.com/v1/oauth/authorize"
_TOKEN_URL = "https://api.notion.com/v1/oauth/token"
_REDIRECT_URI = "http://localhost"


class NotionOAuthManager:
    """Manages Notion OAuth for per-user credentials."""

    def __init__(self, credential_store):
        self._store = credential_store

    @property
    def _client_id(self) -> str:
        return os.environ.get("NOTION_OAUTH_CLIENT_ID", "")

    @property
    def _client_secret(self) -> str:
        return os.environ.get("NOTION_OAUTH_CLIENT_SECRET", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def generate_auth_url(self) -> str | None:
        """Generate the Notion OAuth authorization URL.

        Returns None if client ID/secret are not configured.
        """
        if not self.is_configured:
            return None

        params = {
            "client_id": self._client_id,
            "redirect_uri": _REDIRECT_URI,
            "response_type": "code",
            "owner": "user",
            "state": secrets.token_urlsafe(16),
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, user_id: str, code: str) -> dict:
        """Exchange an authorization code for a token and store it.

        Returns {"ok": True, "workspace_name": ...} on success,
        {"ok": False, "error": str} on failure.
        """
        if not self.is_configured:
            return {"ok": False, "error": "Notion OAuth is not configured (missing NOTION_OAUTH_CLIENT_ID / NOTION_OAUTH_CLIENT_SECRET)"}

        try:
            clean_code = code.strip()
            logger.info("Notion code exchange for user=%s: code=%s...", user_id, clean_code[:20])

            # Notion uses Basic auth: base64(client_id:client_secret)
            credentials = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()

            resp = _requests.post(
                _TOKEN_URL,
                json={
                    "grant_type": "authorization_code",
                    "code": clean_code,
                    "redirect_uri": _REDIRECT_URI,
                },
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            data = resp.json()

            if "error" in data:
                msg = data.get("error_description", data.get("message", data["error"]))
                logger.error("Notion code exchange failed for user=%s: %s (full response: %s)", user_id, msg, data)
                return {"ok": False, "error": msg}

            token_data = {
                "access_token": data["access_token"],
                "workspace_id": data.get("workspace_id", ""),
                "workspace_name": data.get("workspace_name", ""),
                "bot_id": data.get("bot_id", ""),
            }
            self._store.set(user_id, SERVICE_NAME, token_data)
            logger.info("Notion OAuth token stored for user=%s (workspace=%s)", user_id, token_data["workspace_name"])
            return {"ok": True, "workspace_name": token_data["workspace_name"]}

        except Exception as e:
            logger.error("Notion OAuth code exchange failed for user=%s: %s", user_id, e)
            return {"ok": False, "error": str(e)}

    def get_token(self, user_id: str) -> str | None:
        """Get the Notion access token for a user, or None if not connected."""
        token_data = self._store.get(user_id, SERVICE_NAME)
        if not token_data:
            return None
        return token_data.get("access_token")

    def get_workspace_name(self, user_id: str) -> str:
        """Get the workspace name for a connected user."""
        token_data = self._store.get(user_id, SERVICE_NAME)
        if not token_data:
            return ""
        return token_data.get("workspace_name", "")

    def revoke_and_delete(self, user_id: str) -> dict:
        """Delete Notion credentials from store.

        Notion has no revoke endpoint — users revoke from Notion settings.
        """
        token_data = self._store.get(user_id, SERVICE_NAME)
        if not token_data:
            return {"ok": False, "error": "No Notion credentials found for your account."}

        self._store.delete(user_id, SERVICE_NAME)
        logger.info("Notion credentials deleted for user=%s", user_id)
        return {"ok": True}
