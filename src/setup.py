"""
Jibsa Setup Wizard — interactive CLI for first-time configuration.

Walks through environment setup, API keys, and integration configuration.
Idempotent — safe to run again on an existing setup.

Usage:
    python -m src.setup
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_ENV_FILE = _ROOT / ".env"
_ENV_EXAMPLE = _ROOT / ".env.example"
_SETTINGS_FILE = _ROOT / "config" / "settings.yaml"

# ANSI colors
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {_GREEN}✔{_RESET} {msg}")


def _skip(msg: str) -> None:
    print(f"  {_DIM}—{_RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"  {_YELLOW}!{_RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"  {_RED}✖{_RESET} {msg}")


def _section(title: str) -> None:
    print(f"\n{_BOLD}[{title}]{_RESET}")


def _prompt(label: str, default: str = "") -> str:
    """Prompt user for input with an optional default."""
    if default:
        raw = input(f"  {label} [{default}]: ").strip()
        return raw or default
    return input(f"  {label}: ").strip()


def _yes_no(question: str, default: bool = False) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"  {question} {hint}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _read_env() -> dict[str, str]:
    """Parse .env file into a dict (preserving only key=value lines)."""
    env = {}
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def _update_env(key: str, value: str) -> None:
    """Update or append a key=value pair in .env."""
    if not _ENV_FILE.exists():
        _ENV_FILE.write_text("")

    lines = _ENV_FILE.read_text().splitlines()
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
            lines[i] = f"{key}={value}"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}")

    _ENV_FILE.write_text("\n".join(lines) + "\n")


def _is_placeholder(value: str) -> bool:
    """Check if an env value is still a placeholder from .env.example."""
    placeholders = {"your-", "xoxb-your-", "xapp-your-", "sk-ant-your-", "ntn_your-"}
    return any(value.startswith(p) or value == p for p in placeholders) or not value


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step_runtime() -> None:
    _section("Runtime")

    # uv
    if shutil.which("uv"):
        _ok(f"uv found: {shutil.which('uv')}")
    else:
        _warn("uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh")
        return

    # .venv
    venv = _ROOT / ".venv"
    if venv.exists():
        _ok(".venv exists")
    else:
        if _yes_no("Create .venv with Python 3.12?", default=True):
            subprocess.run(["uv", "venv", "--python", "3.12"], cwd=_ROOT, check=True)
            _ok(".venv created")
        else:
            _warn("Skipped .venv creation")
            return

    # Install deps
    reqs = _ROOT / "requirements.txt"
    if reqs.exists():
        if _yes_no("Install dependencies from requirements.txt?", default=True):
            subprocess.run(["uv", "pip", "install", "-r", "requirements.txt", "--quiet"], cwd=_ROOT, check=True)
            _ok("Dependencies installed")
    else:
        _warn("requirements.txt not found — run ./scripts/compile.sh first")


def step_env_file() -> None:
    _section("Environment File")

    if _ENV_FILE.exists():
        _ok(".env exists")
    elif _ENV_EXAMPLE.exists():
        shutil.copy(_ENV_EXAMPLE, _ENV_FILE)
        _ok("Created .env from .env.example")
    else:
        _ENV_FILE.write_text("")
        _warn("Created empty .env (no .env.example found)")


def step_required_keys() -> None:
    _section("Required: Slack Tokens")

    env = _read_env()

    for key, hint in [
        ("SLACK_BOT_TOKEN", "xoxb-..."),
        ("SLACK_APP_TOKEN", "xapp-..."),
    ]:
        current = env.get(key, "")
        if current and not _is_placeholder(current):
            _ok(f"{key} is set ({current[:12]}...)")
        else:
            value = _prompt(f"Enter {key} ({hint})")
            if value:
                _update_env(key, value)
                _ok(f"{key} saved")
            else:
                _warn(f"{key} skipped — you'll need to set it before running Jibsa")


def step_llm_provider() -> None:
    _section("LLM Provider")

    env = _read_env()

    print("  Which LLM provider?")
    print("    [1] Anthropic (default)")
    print("    [2] OpenAI")
    print("    [3] Google")
    choice = _prompt("Choice", "1")

    provider_map = {"1": "anthropic", "2": "openai", "3": "google"}
    key_map = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "google": "GOOGLE_API_KEY"}
    hint_map = {"anthropic": "sk-ant-...", "openai": "sk-...", "google": "AI..."}

    provider = provider_map.get(choice, "anthropic")
    env_key = key_map[provider]
    current = env.get(env_key, "")

    if current and not _is_placeholder(current):
        _ok(f"{env_key} is set ({current[:12]}...)")
    else:
        value = _prompt(f"Enter {env_key} ({hint_map[provider]})")
        if value:
            _update_env(env_key, value)
            _ok(f"{env_key} saved")


def step_optional_integrations() -> None:
    _section("Optional Integrations")

    env = _read_env()

    # Notion
    if _yes_no("Enable Notion Second Brain?"):
        token = env.get("NOTION_TOKEN", "")
        if token and not _is_placeholder(token):
            _ok(f"NOTION_TOKEN is set ({token[:12]}...)")
        else:
            value = _prompt("Enter NOTION_TOKEN (ntn_...)")
            if value:
                _update_env("NOTION_TOKEN", value)
                _ok("NOTION_TOKEN saved")
    else:
        _skip("Notion skipped")

    # Jira + Confluence
    if _yes_no("Enable Jira + Confluence?"):
        for key, hint in [
            ("JIRA_SERVER", "https://your-org.atlassian.net"),
            ("JIRA_EMAIL", "you@example.com"),
            ("JIRA_API_TOKEN", "your-api-token"),
        ]:
            current = env.get(key, "")
            if current and not _is_placeholder(current):
                _ok(f"{key} is set")
            else:
                value = _prompt(f"Enter {key} ({hint})")
                if value:
                    _update_env(key, value)
                    _ok(f"{key} saved")
    else:
        _skip("Jira + Confluence skipped")

    # Google OAuth
    if _yes_no("Enable Google Calendar + Gmail (per-user OAuth)?"):
        for key, hint in [
            ("GOOGLE_CLIENT_ID", "your-client-id.apps.googleusercontent.com"),
            ("GOOGLE_CLIENT_SECRET", "your-client-secret"),
        ]:
            current = env.get(key, "")
            if current and not _is_placeholder(current):
                _ok(f"{key} is set")
            else:
                value = _prompt(f"Enter {key}")
                if value:
                    _update_env(key, value)
                    _ok(f"{key} saved")
    else:
        _skip("Google OAuth skipped")


def step_encryption_key() -> None:
    _section("Credential Encryption Key")

    env = _read_env()
    if env.get("CREDENTIAL_ENCRYPTION_KEY"):
        _ok("CREDENTIAL_ENCRYPTION_KEY is set")
    else:
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        _update_env("CREDENTIAL_ENCRYPTION_KEY", key)
        _ok(f"Generated and saved CREDENTIAL_ENCRYPTION_KEY")


def step_validate() -> None:
    _section("Validation")

    settings_example = _ROOT / "config" / "settings.yaml.example"
    if not _SETTINGS_FILE.exists() and settings_example.exists():
        shutil.copy(settings_example, _SETTINGS_FILE)
        _ok("Created config/settings.yaml from example")

    if _SETTINGS_FILE.exists():
        _ok("config/settings.yaml exists")
        try:
            import yaml
            from src.config_schema import validate_config
            with open(_SETTINGS_FILE) as f:
                validate_config(yaml.safe_load(f))
            _ok("settings.yaml passes validation")
        except Exception as e:
            _fail(f"settings.yaml validation failed: {e}")
    else:
        _fail("config/settings.yaml not found — copy config/settings.yaml.example")


def step_doctor() -> None:
    _section("Health Check")

    print()
    try:
        from src.doctor import run_doctor
        run_doctor()
    except SystemExit:
        pass  # doctor calls sys.exit
    except ImportError:
        _warn("Could not run doctor — import error (dependencies may not be installed yet)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\n{_BOLD}{'='*50}")
    print(f"  Jibsa Setup Wizard")
    print(f"{'='*50}{_RESET}\n")
    print("  This wizard will help you configure Jibsa.")
    print("  Press Enter to accept defaults. Ctrl+C to quit.\n")

    try:
        step_runtime()
        step_env_file()
        step_required_keys()
        step_llm_provider()
        step_optional_integrations()
        step_encryption_key()
        step_validate()
        step_doctor()
    except KeyboardInterrupt:
        print(f"\n\n  {_YELLOW}Setup interrupted. Run again anytime.{_RESET}\n")
        sys.exit(1)

    print(f"\n{_BOLD}{'='*50}")
    print(f"  Setup complete!")
    print(f"{'='*50}{_RESET}\n")
    print(f"  Next steps:")
    print(f"    1. Review .env and config/settings.yaml")
    print(f"    2. ./scripts/run.sh — start Jibsa")
    print(f"    3. Go to #jibsa in Slack and say: help")
    print()


if __name__ == "__main__":
    main()
