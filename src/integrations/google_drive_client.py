"""
GoogleDriveClient — wrapper around the Google Drive API v3.

Uses per-user OAuth credentials. Instantiated per-request with
the requesting user's credentials.
"""
import base64
import logging
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

logger = logging.getLogger(__name__)


class GoogleDriveAPIError(Exception):
    """Wraps Google Drive API errors with operation context."""

    def __init__(self, operation: str, cause: Exception):
        super().__init__(f"Google Drive {operation} failed: {cause}")
        self.cause = cause


# Mime types for Google Workspace documents
_GOOGLE_DOCS_MIME = "application/vnd.google-apps.document"
_GOOGLE_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"
_GOOGLE_SLIDES_MIME = "application/vnd.google-apps.presentation"


class GoogleDriveClient:
    def __init__(self, credentials):
        self._service = build(
            "drive", "v3",
            credentials=credentials,
            cache_discovery=False,
        )

    # -------------------------------------------------------------------
    # Read methods
    # -------------------------------------------------------------------

    def list_files(
        self,
        query: str = "",
        max_results: int = 10,
        folder_id: str | None = None,
    ) -> list[dict]:
        """List files, optionally filtered by query text and/or folder."""
        logger.debug("list_files → query=%s max_results=%d folder_id=%s", query, max_results, folder_id)
        clauses: list[str] = []
        if folder_id:
            clauses.append(f"'{folder_id}' in parents")
        if query:
            clauses.append(f"(name contains '{query}' or fullText contains '{query}')")
        clauses.append("trashed = false")
        q = " and ".join(clauses)
        try:
            response = (
                self._service.files()
                .list(
                    q=q,
                    pageSize=max_results,
                    fields="files(id, name, mimeType, modifiedTime, webViewLink, size, owners)",
                )
                .execute()
            )
            files = response.get("files", [])
            logger.debug("list_files ← %d files", len(files))
            return files
        except HttpError as e:
            logger.error("list_files FAILED query=%s: %s", query, e)
            raise GoogleDriveAPIError("list_files", e) from e

    def search_files(self, query: str, max_results: int = 10) -> list[dict]:
        """Search files by text query."""
        logger.debug("search_files → query=%s max_results=%d", query, max_results)
        return self.list_files(query=query, max_results=max_results)

    def get_file_metadata(self, file_id: str) -> dict:
        """Retrieve metadata for a single file by ID."""
        logger.debug("get_file_metadata → file_id=%s", file_id)
        try:
            metadata = (
                self._service.files()
                .get(
                    fileId=file_id,
                    fields="id, name, mimeType, modifiedTime, webViewLink, size, owners, description",
                )
                .execute()
            )
            logger.debug("get_file_metadata ← name=%s", metadata.get("name"))
            return metadata
        except HttpError as e:
            logger.error("get_file_metadata FAILED file_id=%s: %s", file_id, e)
            raise GoogleDriveAPIError("get_file_metadata", e) from e

    def get_file_content(self, file_id: str, max_chars: int = 8000) -> str:
        """Read file content as text. Google Workspace docs are exported; binary files are skipped."""
        logger.debug("get_file_content → file_id=%s max_chars=%d", file_id, max_chars)
        try:
            # First, get the mime type
            meta = self._service.files().get(fileId=file_id, fields="mimeType").execute()
            mime = meta.get("mimeType", "")

            if mime == _GOOGLE_DOCS_MIME:
                raw = self._service.files().export(fileId=file_id, mimeType="text/plain").execute()
                text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            elif mime == _GOOGLE_SHEETS_MIME:
                raw = self._service.files().export(fileId=file_id, mimeType="text/csv").execute()
                text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            elif mime == _GOOGLE_SLIDES_MIME:
                raw = self._service.files().export(fileId=file_id, mimeType="text/plain").execute()
                text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            else:
                # Try to read as text; binary files will fail decoding
                try:
                    raw = self._service.files().get_media(fileId=file_id).execute()
                    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                except (UnicodeDecodeError, AttributeError):
                    logger.debug("get_file_content ← binary file, cannot display")
                    return "Binary file — cannot display content"

            truncated = text[:max_chars]
            logger.debug("get_file_content ← %d chars (truncated=%s)", len(truncated), len(text) > max_chars)
            return truncated
        except HttpError as e:
            logger.error("get_file_content FAILED file_id=%s: %s", file_id, e)
            raise GoogleDriveAPIError("get_file_content", e) from e

    # -------------------------------------------------------------------
    # Write methods
    # -------------------------------------------------------------------

    def create_file(
        self,
        name: str,
        content: str,
        mime_type: str = "text/plain",
        folder_id: str | None = None,
    ) -> dict:
        """Create a new file with text content."""
        logger.debug("create_file → name=%s mime_type=%s folder_id=%s", name, mime_type, folder_id)
        body: dict[str, Any] = {"name": name, "mimeType": mime_type}
        if folder_id:
            body["parents"] = [folder_id]
        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype=mime_type, resumable=False)
        try:
            file = (
                self._service.files()
                .create(body=body, media_body=media, fields="id, name, webViewLink")
                .execute()
            )
            logger.debug("create_file ← id=%s", file.get("id"))
            return file
        except HttpError as e:
            logger.error("create_file FAILED name=%s: %s", name, e)
            raise GoogleDriveAPIError("create_file", e) from e

    def upload_file(
        self,
        name: str,
        content: bytes,
        mime_type: str,
        folder_id: str | None = None,
    ) -> dict:
        """Upload a file with raw bytes content."""
        logger.debug("upload_file → name=%s mime_type=%s folder_id=%s", name, mime_type, folder_id)
        body: dict[str, Any] = {"name": name, "mimeType": mime_type}
        if folder_id:
            body["parents"] = [folder_id]
        media = MediaInMemoryUpload(content, mimetype=mime_type, resumable=False)
        try:
            file = (
                self._service.files()
                .create(body=body, media_body=media, fields="id, name, webViewLink")
                .execute()
            )
            logger.debug("upload_file ← id=%s", file.get("id"))
            return file
        except HttpError as e:
            logger.error("upload_file FAILED name=%s: %s", name, e)
            raise GoogleDriveAPIError("upload_file", e) from e

    # -------------------------------------------------------------------
    # Action plan dispatch
    # -------------------------------------------------------------------

    def execute_step(self, step: dict) -> dict:
        """Dispatch a single action plan step. Always returns a dict -- never raises."""
        action = step.get("action", "")
        params = step.get("params", {})
        description = step.get("description", action)

        handlers = {
            "create_file": self._handle_create_file,
            "upload_file": self._handle_upload_file,
        }

        handler = handlers.get(action)
        if not handler:
            return {"ok": False, "error": f"Unknown action: {action}", "description": description}

        try:
            result = handler(params)
            result["description"] = description
            return result
        except GoogleDriveAPIError as e:
            logger.error("Google Drive step %s failed: %s", action, e)
            return {"ok": False, "error": str(e), "description": description}
        except Exception as e:
            logger.error("Unexpected error in step %s: %s", action, e)
            return {"ok": False, "error": f"Unexpected error: {e}", "description": description}

    # -------------------------------------------------------------------
    # Step handlers
    # -------------------------------------------------------------------

    def _handle_create_file(self, params: dict) -> dict:
        file = self.create_file(
            name=params["name"],
            content=params.get("content", ""),
            mime_type=params.get("mime_type", "text/plain"),
            folder_id=params.get("folder_id"),
        )
        return {
            "ok": True,
            "file_id": file.get("id", ""),
            "url": file.get("webViewLink", ""),
        }

    def _handle_upload_file(self, params: dict) -> dict:
        raw_content = base64.b64decode(params["content"])
        file = self.upload_file(
            name=params["name"],
            content=raw_content,
            mime_type=params.get("mime_type", "application/octet-stream"),
            folder_id=params.get("folder_id"),
        )
        return {
            "ok": True,
            "file_id": file.get("id", ""),
            "url": file.get("webViewLink", ""),
        }
