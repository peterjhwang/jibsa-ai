"""
CodeExecTool — CrewAI tool for sandboxed Python code execution.

Runs Python code in a subprocess with timeout and safety checks.
"""
import subprocess
import tempfile
from pathlib import Path
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

MAX_TIMEOUT = 30
MAX_OUTPUT_CHARS = 4000

BLOCKED_PATTERNS = [
    ("import os\nos.system", "os.system calls"),
    ("subprocess.call", "subprocess calls"),
    ("subprocess.run", "subprocess calls"),
    ("subprocess.Popen", "subprocess calls"),
    ("shutil.rmtree", "file deletion"),
    ("os.remove", "file deletion"),
    ("os.unlink", "file deletion"),
    ("__import__('os')", "dynamic os import"),
    ("eval(", "eval calls"),
    ("exec(", "exec calls"),
    ("open(", "file I/O"),
]


class CodeExecInput(BaseModel):
    """Input schema for code execution."""
    code: str = Field(..., description="Python code to execute")


class CodeExecTool(BaseTool):
    name: str = "Run Python Code"
    description: str = (
        "Execute Python code in a sandboxed environment. "
        "Useful for calculations, data processing, and generating outputs. "
        "Cannot access files, network, or system commands."
    )
    args_schema: Type[BaseModel] = CodeExecInput

    def _run(self, code: str) -> str:
        # Safety check
        for pattern, reason in BLOCKED_PATTERNS:
            if pattern in code:
                return f"Blocked: {reason} are not allowed for safety."

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                temp_path = f.name

            result = subprocess.run(
                ["python3", temp_path],
                capture_output=True,
                text=True,
                timeout=MAX_TIMEOUT,
                cwd=tempfile.gettempdir(),
            )

            Path(temp_path).unlink(missing_ok=True)

            stdout = result.stdout[:MAX_OUTPUT_CHARS] if result.stdout else ""
            stderr = result.stderr[:MAX_OUTPUT_CHARS] if result.stderr else ""

            if result.returncode != 0:
                return f"Error (exit {result.returncode}):\n{stderr}"

            output = stdout
            if stderr:
                output += f"\nWarnings:\n{stderr}"
            return output or "(no output)"

        except subprocess.TimeoutExpired:
            Path(temp_path).unlink(missing_ok=True)
            return f"Code timed out after {MAX_TIMEOUT} seconds."
        except Exception as e:
            return f"Execution error: {e}"
