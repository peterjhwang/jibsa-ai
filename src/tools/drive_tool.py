"""
DriveReadTool — CrewAI tool for searching and reading Google Drive files.

Uses per-user OAuth credentials via the current_user_id ContextVar.
Write operations (create/upload) go through the propose-approve flow.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from ..context import current_user_id

if TYPE_CHECKING:
    from ..integrations.google_oauth import GoogleOAuthManager

logger = logging.getLogger(__name__)


class DriveQueryInput(BaseModel):
    """Input schema for Drive queries."""
    query: str = Field(
        ...,
        description=(
            "Search query for Google Drive files (e.g. 'quarterly report', "
            "'meeting notes', 'budget spreadsheet')"
        ),
    )


class DriveReadTool(BaseTool):
    name: str = "Google Drive"
    description: str = (
        "Search and read files in Google Drive. Returns file names, types, "
        "and content for documents. To create files in Drive, propose an "
        "action plan. Requires the user to have connected Google."
    )
    args_schema: Type[BaseModel] = DriveQueryInput
    google_oauth: object = None

    def _run(self, query: str) -> str:
        user_id = current_user_id.get()
        if not user_id:
            return "Could not determine the requesting user."

        if not self.google_oauth:
            return "Google Drive is not configured."

        creds = self.google_oauth.get_valid_credentials(user_id)
        if not creds:
            return (
                "You haven't connected Google yet. "
                "Say `connect google` to link your account."
            )

        try:
            from ..integrations.google_drive_client import GoogleDriveClient
            client = GoogleDriveClient(creds)

            files = client.search_files(query, max_results=10)
            return self._format_files(files, query)

        except Exception as e:
            logger.warning("Drive query failed for user %s: %s", user_id, e)
            return f"Drive search failed: {e}"

    @staticmethod
    def _format_files(files: list[dict], query: str) -> str:
        if not files:
            return f"No files found for: {query}"

        lines = [f"*Drive results* ({len(files)} files):\n"]
        for f in files:
            name = f.get("name", "Untitled")
            mime = f.get("mimeType", "")
            modified = f.get("modifiedTime", "")[:10]
            url = f.get("webViewLink", "")

            # Friendly type name
            type_map = {
                "application/vnd.google-apps.document": "Doc",
                "application/vnd.google-apps.spreadsheet": "Sheet",
                "application/vnd.google-apps.presentation": "Slides",
                "application/vnd.google-apps.folder": "Folder",
                "application/pdf": "PDF",
            }
            file_type = type_map.get(mime, mime.split("/")[-1] if "/" in mime else "File")

            url_str = f" | <{url}|open>" if url else ""
            lines.append(f"  - *{name}* [{file_type}] modified {modified}{url_str}")

        return "\n".join(lines)

    @classmethod
    def create(cls, google_oauth: GoogleOAuthManager) -> DriveReadTool:
        tool = cls()
        tool.google_oauth = google_oauth
        return tool
