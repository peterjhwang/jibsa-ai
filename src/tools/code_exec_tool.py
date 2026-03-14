"""
CodeExecTool — CrewAI tool for sandboxed Python code execution.

Runs Python code in a subprocess with timeout and safety checks.

Security: Two-layer defence:
  1. Regex patterns catch common dangerous calls (fast, first pass).
  2. AST analysis catches obfuscation (unicode escapes, compile(), attribute
     introspection) that regex alone would miss.
"""
import ast
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
    (r"\bcompile\s*\(", "compile calls"),
    (r"\bglobals\s*\(", "globals access"),
    (r"\blocals\s*\(", "locals access"),
    (r"\bbreakpoint\s*\(", "debugger access"),
    (r"\b__subclasses__\b", "class introspection"),
    (r"\b__bases__\b", "class introspection"),
    (r"\b__mro__\b", "class introspection"),
    (r"\bctypes\b", "ctypes access"),
    (r"\bsignal\b", "signal module access"),
]

# Blocked module names in import statements (AST-level check)
BLOCKED_MODULES = frozenset({
    "os", "sys", "subprocess", "shutil", "importlib", "ctypes", "signal",
    "socket", "urllib", "http", "requests", "httpx", "pathlib", "io",
    "builtins", "code", "codeop", "compileall", "py_compile",
    "multiprocessing", "threading", "concurrent", "_thread",
})

# Blocked function names when called directly
BLOCKED_CALLS = frozenset({
    "eval", "exec", "compile", "open", "breakpoint",
    "__import__", "globals", "locals", "getattr", "setattr", "delattr",
    "vars", "dir", "type", "memoryview",
})

# Blocked dunder attributes
BLOCKED_ATTRS = frozenset({
    "__import__", "__builtins__", "__subclasses__", "__bases__", "__mro__",
    "__globals__", "__code__", "__class__", "__dict__",
})


def _ast_check(code: str) -> str | None:
    """Parse code as AST and check for unsafe patterns. Returns reason or None."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None  # let subprocess report the syntax error

    for node in ast.walk(tree):
        # Block dangerous imports
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in BLOCKED_MODULES:
                    return f"import of '{name}' module"

        # Block dangerous function calls
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BLOCKED_CALLS:
                return f"call to '{func.id}()'"
            if isinstance(func, ast.Attribute) and func.attr in BLOCKED_CALLS:
                return f"call to '.{func.attr}()'"

        # Block access to dangerous dunder attributes
        if isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRS:
            return f"access to '{node.attr}'"

    return None


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
        # Layer 1: Regex-based blocking (fast, catches plain-text patterns)
        for pattern, reason in BLOCKED_PATTERNS:
            if re.search(pattern, code):
                return f"Blocked: {reason} are not allowed for safety."

        # Layer 2: AST-based blocking (catches obfuscation, unicode escapes, etc.)
        ast_reason = _ast_check(code)
        if ast_reason:
            return f"Blocked: {ast_reason} is not allowed for safety."

        temp_path = None
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

            stdout = result.stdout[:self.max_output_chars] if result.stdout else ""
            stderr = result.stderr[:self.max_output_chars] if result.stderr else ""

            if result.returncode != 0:
                return f"Error (exit {result.returncode}):\n{stderr}"

            output = stdout
            if stderr:
                output += f"\nWarnings:\n{stderr}"
            return output or "(no output)"

        except subprocess.TimeoutExpired:
            return f"Code timed out after {self.timeout} seconds."
        except Exception as e:
            return f"Execution error: {e}"
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)
