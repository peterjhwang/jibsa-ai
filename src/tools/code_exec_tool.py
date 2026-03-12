"""
CodeExecTool — CrewAI tool for sandboxed Python code execution.

Runs Python code in a subprocess with timeout and safety checks.
"""
import re
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_OUTPUT_CHARS = 4000

# Patterns checked via regex for more robust blocking (catches obfuscation attempts)
BLOCKED_PATTERNS: list[tuple[str, str]] = [
    (r"\bos\.system\b", "os.system calls"),
    (r"\bsubprocess\b", "subprocess usage"),
    (r"\bshutil\.rmtree\b", "file deletion"),
    (r"\bos\.remove\b", "file deletion"),
    (r"\bos\.unlink\b", "file deletion"),
    (r"__import__\s*\(", "dynamic imports"),
    (r"\beval\s*\(", "eval calls"),
    (r"\bexec\s*\(", "exec calls"),
    (r"\bopen\s*\(", "file I/O"),
    (r"\bimportlib\b", "dynamic imports"),
    (r"\bgetattr\s*\(\s*__builtins__", "builtins access"),
    (r"\bos\.environ\b", "environment variable access"),
    (r"\bsocket\b", "network access"),
    (r"\burllib\b", "network access"),
    (r"\brequests\b", "network access"),
    (r"\bhttpx\b", "network access"),
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

    timeout: int = DEFAULT_TIMEOUT
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS

    @classmethod
    def create(cls, config: dict | None = None) -> "CodeExecTool":
        """Create with optional config overrides."""
        if not config:
            return cls()
        jibsa_cfg = config.get("jibsa", {})
        return cls(
            timeout=jibsa_cfg.get("code_exec_timeout", DEFAULT_TIMEOUT),
            max_output_chars=jibsa_cfg.get("code_exec_max_output", DEFAULT_MAX_OUTPUT_CHARS),
        )

    def _run(self, code: str) -> str:
        # Safety check — regex-based for robustness
        for pattern, reason in BLOCKED_PATTERNS:
            if re.search(pattern, code):
                return f"Blocked: {reason} are not allowed for safety."

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                temp_path = f.name

            result = subprocess.run(
                ["python3", temp_path],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=tempfile.gettempdir(),
            )

            Path(temp_path).unlink(missing_ok=True)

            stdout = result.stdout[:self.max_output_chars] if result.stdout else ""
            stderr = result.stderr[:self.max_output_chars] if result.stderr else ""

            if result.returncode != 0:
                return f"Error (exit {result.returncode}):\n{stderr}"

            output = stdout
            if stderr:
                output += f"\nWarnings:\n{stderr}"
            return output or "(no output)"

        except subprocess.TimeoutExpired:
            Path(temp_path).unlink(missing_ok=True)
            return f"Code timed out after {self.timeout} seconds."
        except Exception as e:
            return f"Execution error: {e}"
