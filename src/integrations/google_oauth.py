"""
GoogleOAuthManager — handles per-user Google OAuth2 flows.

Since Jibsa runs in Slack Socket Mode (no public URL), we use the
"installed app" / OOB-style flow:
  1. Bot generates an auth URL and DMs it to the user
  2. User visits the URL, authorizes, and copies the code
  3. User pastes the code back in the DM
  4. Bot exchanges code for tokens and stores them encrypted

Tokens are stored via CredentialStore (encrypted SQLite).
Access tokens are refreshed transparently when expired.
"""
import logging
import os
from datetime import datetime, timezone

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

logger = logging.getLogger(__name__)

SERVICE_NAME = "google"

# Default scopes — can be overridden via config
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

# OOB redirect URI for installed apps
_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


class GoogleOAuthManager:
    """Manages Google OAuth2 for per-user credentials."""

    def __init__(self, credential_store, scopes: list[str] | None = None):
        self._store = credential_store
        self._scopes = scopes or DEFAULT_SCOPES
        self._client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        self._client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    @property
    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def _build_flow(self) -> Flow:
        """Build a google_auth_oauthlib Flow from env vars."""
        client_config = {
            "installed": {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [_REDIRECT_URI],
            }
        }
        flow = Flow.from_client_config(client_config, scopes=self._scopes)
        flow.redirect_uri = _REDIRECT_URI
        return flow

    def generate_auth_url(self) -> str | None:
        """Generate the Google OAuth authorization URL.

        Returns None if client ID/secret are not configured.
        """
        if not self.is_configured:
            return None

        flow = self._build_flow()
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",  # force refresh_token on every auth
        )
        return auth_url

    def exchange_code(self, user_id: str, code: str) -> dict:
        """Exchange an authorization code for tokens and store them.

        Returns {"ok": True} on success, {"ok": False, "error": str} on failure.
        """
        if not self.is_configured:
            return {"ok": False, "error": "Google OAuth is not configured (missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)"}

        try:
            flow = self._build_flow()
            flow.fetch_token(code=code)
            creds = flow.credentials

            token_data = {
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes or self._scopes),
                "expiry": creds.expiry.isoformat() if creds.expiry else None,
            }

            self._store.set(user_id, SERVICE_NAME, token_data)
            logger.info("Google OAuth tokens stored for user=%s", user_id)
            return {"ok": True}

        except Exception as e:
            logger.error("Google OAuth code exchange failed for user=%s: %s", user_id, e)
            return {"ok": False, "error": str(e)}

    def get_valid_credentials(self, user_id: str) -> Credentials | None:
        """Get valid Google credentials for a user, refreshing if needed.

        Returns None if user has no stored credentials or refresh fails.
        """
        token_data = self._store.get(user_id, SERVICE_NAME)
        if not token_data:
            return None

        creds = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id", self._client_id),
            client_secret=token_data.get("client_secret", self._client_secret),
            scopes=token_data.get("scopes", self._scopes),
        )

        # Parse expiry
        expiry_str = token_data.get("expiry")
        if expiry_str:
            try:
                creds.expiry = datetime.fromisoformat(expiry_str).replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        # Refresh if expired
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(google.auth.transport.requests.Request())
                # Store refreshed tokens
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
        """Revoke Google tokens and delete from store.

        Returns {"ok": True} or {"ok": False, "error": str}.
        """
        token_data = self._store.get(user_id, SERVICE_NAME)
        if not token_data:
            return {"ok": False, "error": "No Google credentials found for your account."}

        # Attempt to revoke the token at Google
        access_token = token_data.get("access_token", "")
        if access_token:
            try:
                import requests
                resp = requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": access_token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    logger.info("Revoked Google token for user=%s", user_id)
                else:
                    logger.warning(
                        "Google token revocation returned %d for user=%s (continuing with deletion)",
                        resp.status_code, user_id,
                    )
            except Exception as e:
                logger.warning("Google token revocation failed for user=%s: %s (continuing with deletion)", user_id, e)

        self._store.delete(user_id, SERVICE_NAME)
        return {"ok": True}
