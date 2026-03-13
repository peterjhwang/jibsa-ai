"""
FileGenTool — CrewAI tool for generating and uploading files to Slack.

This is a write tool — the agent proposes a file generation action plan,
the user approves, and the orchestrator creates + uploads the file.
"""
import csv
import io
import json
import logging
import tempfile
from pathlib import Path
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {"csv", "json", "md", "txt"}


class FileGenInput(BaseModel):
    """Input schema for file generation."""
    filename: str = Field(..., description="Output filename (e.g. 'tasks.csv', 'report.md')")
    content: str = Field(..., description="The file content. For CSV, use comma-separated rows. For JSON, provide valid JSON. For md/txt, provide the text.")
    title: str = Field(default="", description="Optional title/description for the Slack upload")


class FileGenTool(BaseTool):
    name: str = "Generate File"
    description: str = (
        "Generate a file (CSV, JSON, Markdown, or plain text) and upload it to Slack. "
        "This requires approval — the file will be proposed first, then uploaded after confirmation. "
        "Supported formats: .csv, .json, .md, .txt"
    )
    args_schema: Type[BaseModel] = FileGenInput

    def _run(self, filename: str, content: str, title: str = "") -> str:
        # Validate format
        ext = Path(filename).suffix.lstrip(".").lower()
        if ext not in SUPPORTED_FORMATS:
            return f"Unsupported format '.{ext}'. Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"

        # Return instructions for the agent to propose an action plan
        desc = title or f"Generate {filename}"
        return (
            f"To generate and upload this file, propose an action plan with:\n"
            f'{{"type": "action_plan", "summary": "{desc}", '
            f'"steps": [{{"service": "file_gen", "action": "upload_file", '
            f'"params": {{"filename": "{filename}", "content": <the file content>, "title": "{desc}"}}, '
            f'"description": "Generate and upload {filename}"}}], '
            f'"needs_approval": true}}'
        )


def create_and_get_path(filename: str, content: str) -> str:
    """Create the file in a temp directory and return the path.

    Called by the orchestrator during plan execution (after approval).
    """
    ext = Path(filename).suffix.lstrip(".").lower()

    tmp_dir = tempfile.mkdtemp(prefix="jibsa_files_")
    file_path = Path(tmp_dir) / filename

    if ext == "json":
        # Try to pretty-print if it's valid JSON
        try:
            parsed = json.loads(content)
            content = json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass  # Write as-is

    file_path.write_text(content, encoding="utf-8")
    return str(file_path)
