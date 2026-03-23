"""
CredentialStore — encrypted per-user credential storage backed by SQLite.

Stores OAuth tokens and other sensitive per-user data, encrypted at rest
using Fernet (AES-128-CBC + HMAC-SHA256). The master key is read from
the CREDENTIAL_ENCRYPTION_KEY env var.

Thread-safe: uses a lock for all write operations.
"""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "data/credentials.db"


class CredentialStore:
    """Encrypted SQLite store for per-user service credentials."""

    def __init__(self, db_path: str | None = None, encryption_key: str | None = None):
        self._db_path = db_path or os.environ.get("CREDENTIAL_DB_PATH", _DEFAULT_DB_PATH)
        self._lock = threading.Lock()

        # Resolve encryption key
        key = encryption_key or os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
        if not key:
            key = Fernet.generate_key().decode()
            logger.warning(
                "CREDENTIAL_ENCRYPTION_KEY is not set — generated a temporary key. "
                "Credentials will be UNREADABLE after restart. "
                "Set CREDENTIAL_ENCRYPTION_KEY in .env for persistence. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

        # Ensure parent directory exists
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    slack_user_id TEXT NOT NULL,
                    service       TEXT NOT NULL,
                    encrypted_tokens BLOB NOT NULL,
                    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                    expires_at    TEXT,
                    PRIMARY KEY (slack_user_id, service)
                )
            """)
            self._conn.commit()

    def set(self, user_id: str, service: str, token_data: dict) -> None:
        """Store (or update) encrypted credentials for a user+service."""
        plaintext = json.dumps(token_data).encode()
        encrypted = self._fernet.encrypt(plaintext)

        expires_at = token_data.get("expiry") or token_data.get("expires_at")

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO credentials (slack_user_id, service, encrypted_tokens, created_at, expires_at)
                VALUES (?, ?, ?, datetime('now'), ?)
                ON CONFLICT(slack_user_id, service) DO UPDATE SET
                    encrypted_tokens = excluded.encrypted_tokens,
                    expires_at = excluded.expires_at
                """,
                (user_id, service, encrypted, expires_at),
            )
            self._conn.commit()
        logger.debug("Stored credentials for user=%s service=%s", user_id, service)

    def get(self, user_id: str, service: str) -> dict | None:
        """Retrieve and decrypt credentials. Returns None if not found or decryption fails."""
        row = self._conn.execute(
            "SELECT encrypted_tokens FROM credentials WHERE slack_user_id = ? AND service = ?",
            (user_id, service),
        ).fetchone()

        if not row:
            return None

        try:
            plaintext = self._fernet.decrypt(row["encrypted_tokens"])
            return json.loads(plaintext)
        except InvalidToken:
            logger.error(
                "Failed to decrypt credentials for user=%s service=%s — "
                "encryption key may have changed",
                user_id, service,
            )
            return None

    def delete(self, user_id: str, service: str) -> bool:
        """Delete credentials for a user+service. Returns True if a row was deleted."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM credentials WHERE slack_user_id = ? AND service = ?",
                (user_id, service),
            )
            self._conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.debug("Deleted credentials for user=%s service=%s", user_id, service)
        return deleted

    def list_services(self, user_id: str) -> list[str]:
        """Return list of service names the user has stored credentials for."""
        rows = self._conn.execute(
            "SELECT service FROM credentials WHERE slack_user_id = ?",
            (user_id,),
        ).fetchall()
        return [row["service"] for row in rows]

    def list_users_for_service(self, service: str) -> list[str]:
        """Return all user IDs that have credentials for a given service."""
        rows = self._conn.execute(
            "SELECT slack_user_id FROM credentials WHERE service = ?",
            (service,),
        ).fetchall()
        return [row["slack_user_id"] for row in rows]

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()
        logger.debug("CredentialStore closed")
