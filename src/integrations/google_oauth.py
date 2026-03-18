"""
GoogleOAuthManager — handles per-user Google OAuth2 credentials.

Desktop app OAuth client flow:
  1. User runs scripts/google_auth.py on their local machine (opens browser)
  2. User pastes the resulting token JSON into Slack: @Jibsa google token {...}
  3. Bot stores the tokens encrypted via CredentialStore

Tokens are stored via CredentialStore (encrypted SQLite).
Access tokens are refreshed transparently when expired.
"""
import logging
import os
from datetime import datetime

import google.auth.transport.requests
import requests as _requests
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

SERVICE_NAME = "google"

DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleOAuthManager:
    """Manages Google OAuth2 for per-user credentials."""

    def __init__(self, credential_store, scopes: list[str] | None = None):
        self._store = credential_store
        self._scopes = scopes or DEFAULT_SCOPES

    @property
    def _client_id(self) -> str:
        return os.environ.get("GOOGLE_CLIENT_ID", "")

    @property
    def _client_secret(self) -> str:
        return os.environ.get("GOOGLE_CLIENT_SECRET", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def get_valid_credentials(self, user_id: str) -> Credentials | None:
        """Get valid Google credentials for a user, refreshing if needed."""
        token_data = self._store.get(user_id, SERVICE_NAME)
        if not token_data:
            return None

        creds = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", _TOKEN_URL),
            client_id=token_data.get("client_id", self._client_id),
            client_secret=token_data.get("client_secret", self._client_secret),
            scopes=token_data.get("scopes", self._scopes),
        )

        expiry_str = token_data.get("expiry")
        if expiry_str:
            try:
                creds.expiry = datetime.fromisoformat(expiry_str).replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(google.auth.transport.requests.Request())
                updated = {
                    **token_data,
                    "access_token": creds.token,
                    "expiry": creds.expiry.isoformat() if creds.expiry else None,
                }
                self._store.set(user_id, SERVICE_NAME, updated)
                logger.debug("Refreshed Google tokens for user=%s", user_id)
            except Exception as e:
                logger.error("Google token refresh failed for user=%s: %s", user_id, e)
                return None

        return creds

    def revoke_and_delete(self, user_id: str) -> dict:
        """Revoke Google tokens and delete from store."""
        token_data = self._store.get(user_id, SERVICE_NAME)
        if not token_data:
            return {"ok": False, "error": "No Google credentials found for your account."}

        access_token = token_data.get("access_token", "")
        if access_token:
            try:
                resp = _requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": access_token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=10,
                )
                if resp.status_code != 200:
                    logger.warning("Google token revocation returned %d for user=%s", resp.status_code, user_id)
            except Exception as e:
                logger.warning("Google token revocation failed for user=%s: %s", user_id, e)

        self._store.delete(user_id, SERVICE_NAME)
        return {"ok": True}
