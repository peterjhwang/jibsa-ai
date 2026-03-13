"""
Jibsa Doctor — health check CLI.

Validates configuration, environment variables, API connectivity,
and integration setup. Run via:

    python -m src.doctor

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""
import importlib
import logging
import os
import sys
from pathlib import Path

import yaml

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"

# ANSI colors for terminal output
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _pass(msg: str) -> bool:
    print(f"  {_GREEN}✓{_RESET} {msg}")
    return True


def _fail(msg: str) -> bool:
    print(f"  {_RED}✗{_RESET} {msg}")
    return False


def _warn(msg: str) -> bool:
    print(f"  {_YELLOW}!{_RESET} {msg}")
    return True  # warnings don't fail the check


def _section(title: str) -> None:
    print(f"\n{_BOLD}{title}{_RESET}")


def check_config() -> bool:
    """Validate settings.yaml against pydantic schema."""
    _section("Configuration")
    config_path = _CONFIG_DIR / "settings.yaml"

    if not config_path.exists():
        return _fail(f"Config file not found: {config_path}")

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return _fail(f"Invalid YAML: {e}")

    if not raw:
        return _fail("Config file is empty")

    try:
        from .config_schema import validate_config
        settings = validate_config(raw)
        _pass(f"settings.yaml is valid (provider={settings.llm.provider}, model={settings.llm.model})")
        return True
    except Exception as e:
        return _fail(f"Config validation failed: {e}")


def check_env_vars() -> bool:
    """Check required environment variables."""
    _section("Environment Variables")
    all_ok = True

    required = {
        "SLACK_BOT_TOKEN": "Slack Bot OAuth token (xoxb-...)",
        "SLACK_APP_TOKEN": "Slack App-level token for Socket Mode (xapp-...)",
    }

    for var, desc in required.items():
        val = os.environ.get(var, "")
        if val:
            masked = val[:8] + "..." + val[-4:] if len(val) > 12 else "***"
            _pass(f"{var} is set ({masked})")
        else:
            _fail(f"{var} is missing — {desc}")
            all_ok = False

    # LLM provider key
    config_path = _CONFIG_DIR / "settings.yaml"
    provider = "anthropic"
    if config_path.exists():
        try:
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            provider = raw.get("llm", {}).get("provider", "anthropic")
        except Exception:
            pass

    provider_keys = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
    }
    key_var = provider_keys.get(provider, f"{provider.upper()}_API_KEY")
    if os.environ.get(key_var):
        _pass(f"{key_var} is set (LLM provider: {provider})")
    else:
        _fail(f"{key_var} is missing — required for LLM provider '{provider}'")
        all_ok = False

    # Optional keys
    optional = {
        "NOTION_TOKEN": "Notion integration token (ntn_...)",
        "GOOGLE_API_KEY": "Google API key (for image generation)",
        "ZENROWS_API_KEY": "ZenRows API key (for web reader tool)",
    }
    for var, desc in optional.items():
        if var == key_var:
            continue  # already checked above
        val = os.environ.get(var, "")
        if val:
            _pass(f"{var} is set")
        else:
            _warn(f"{var} is not set — {desc} (optional)")

    return all_ok


def check_slack() -> bool:
    """Test Slack API connectivity and bot permissions."""
    _section("Slack Connection")

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        return _fail("Skipped — SLACK_BOT_TOKEN not set")

    try:
        from slack_sdk import WebClient
        client = WebClient(token=token)
        result = client.auth_test()

        if result.get("ok"):
            bot_name = result.get("user", "unknown")
            team = result.get("team", "unknown")
            _pass(f"Connected as @{bot_name} in workspace '{team}'")
        else:
            return _fail(f"auth_test failed: {result.get('error', 'unknown')}")

    except ImportError:
        return _fail("slack_sdk not installed — run: pip install slack-sdk")
    except Exception as e:
        return _fail(f"Slack connection failed: {e}")

    # Check required scopes
    try:
        scopes = result.get("response_metadata", {}).get("scopes", [])
        # Headers may also contain scopes
        if not scopes:
            _warn("Could not verify bot scopes (check Slack app config manually)")
        else:
            required_scopes = {"chat:write", "app_mentions:read"}
            missing = required_scopes - set(scopes)
            if missing:
                _fail(f"Missing bot scopes: {', '.join(missing)}")
            else:
                _pass(f"Bot scopes OK ({len(scopes)} scopes granted)")
    except Exception:
        _warn("Could not verify bot scopes")

    return True


def check_notion() -> bool:
    """Test Notion API connectivity and database access."""
    _section("Notion Integration")

    token = os.environ.get("NOTION_TOKEN")
    if not token:
        _warn("NOTION_TOKEN not set — Notion integration disabled")
        return True  # not a failure if intentionally disabled

    try:
        from notion_client import Client as NotionSDKClient
        client = NotionSDKClient(auth=token)

        # Test authentication
        me = client.users.me()
        bot_name = me.get("name", "unknown")
        _pass(f"Notion authenticated as '{bot_name}'")

    except ImportError:
        return _fail("notion-client not installed — run: pip install notion-client")
    except Exception as e:
        error_str = str(e)
        if "unauthorized" in error_str.lower() or "401" in error_str:
            return _fail("Notion token is invalid or expired")
        return _fail(f"Notion connection failed: {e}")

    # Check for Interns database
    try:
        from .integrations.notion_second_brain import build_second_brain

        config_path = _CONFIG_DIR / "settings.yaml"
        config = {}
        if config_path.exists():
            with open(config_path) as f:
                config = yaml.safe_load(f) or {}

        brain = build_second_brain(config)
        if brain:
            db_id = brain._get_db_id("Interns")
            if db_id:
                _pass("Interns database found in Notion")
            else:
                _warn("Interns database not found — run hire flow to auto-create, or create manually")
        else:
            _warn("Could not initialize Notion Second Brain")
    except Exception as e:
        _warn(f"Could not verify Interns database: {e}")

    return True


def check_llm() -> bool:
    """Test LLM API reachability with a minimal request."""
    _section("LLM Provider")

    config_path = _CONFIG_DIR / "settings.yaml"
    provider = "anthropic"
    model = "claude-sonnet-4-20250514"
    if config_path.exists():
        try:
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            provider = raw.get("llm", {}).get("provider", "anthropic")
            model = raw.get("llm", {}).get("model", model)
        except Exception:
            pass

    _pass(f"Configured: {provider}/{model}")

    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return _fail("ANTHROPIC_API_KEY not set — cannot test connectivity")
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            # Minimal request to test connectivity
            resp = client.messages.create(
                model=model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Say OK"}],
            )
            _pass(f"LLM reachable — test response received ({resp.usage.input_tokens} input tokens)")
            return True
        except ImportError:
            return _fail("anthropic SDK not installed — run: pip install anthropic")
        except Exception as e:
            return _fail(f"LLM test failed: {e}")

    elif provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            return _fail("OPENAI_API_KEY not set — cannot test connectivity")
        try:
            import openai
            client = openai.OpenAI(api_key=key)
            resp = client.chat.completions.create(
                model=model,
                max_tokens=10,
                messages=[{"role": "user", "content": "Say OK"}],
            )
            _pass("LLM reachable — test response received")
            return True
        except ImportError:
            return _fail("openai SDK not installed — run: pip install openai")
        except Exception as e:
            return _fail(f"LLM test failed: {e}")

    else:
        _warn(f"No connectivity test implemented for provider '{provider}'")
        return True


def check_dependencies() -> bool:
    """Check that optional Python dependencies are importable."""
    _section("Python Dependencies")
    all_ok = True

    required_deps = {
        "slack_bolt": "slack-bolt (Slack framework)",
        "slack_sdk": "slack-sdk (Slack API client)",
        "yaml": "pyyaml (config parsing)",
        "crewai": "crewai (AI agent framework)",
        "pydantic": "pydantic (config validation)",
        "apscheduler": "apscheduler (scheduler)",
    }

    optional_deps = {
        "duckduckgo_search": "duckduckgo-search (web search tool)",
        "notion_client": "notion-client (Notion integration)",
        "anthropic": "anthropic (Anthropic LLM provider)",
        "dotenv": "python-dotenv (env file loading)",
    }

    for module, desc in required_deps.items():
        try:
            importlib.import_module(module)
            _pass(f"{desc}")
        except ImportError:
            _fail(f"{desc} — NOT INSTALLED")
            all_ok = False

    for module, desc in optional_deps.items():
        try:
            importlib.import_module(module)
            _pass(f"{desc}")
        except ImportError:
            _warn(f"{desc} — not installed (optional)")

    return all_ok


def run_doctor() -> int:
    """Run all health checks. Returns 0 if all pass, 1 if any fail."""
    print(f"\n{_BOLD}🩺 Jibsa Doctor{_RESET}")
    print("=" * 40)

    # Load .env if present
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    results = []
    results.append(("Config", check_config()))
    results.append(("Environment", check_env_vars()))
    results.append(("Dependencies", check_dependencies()))
    results.append(("Slack", check_slack()))
    results.append(("Notion", check_notion()))
    results.append(("LLM", check_llm()))

    # Summary
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{'=' * 40}")

    if passed == total:
        print(f"{_GREEN}{_BOLD}All {total} checks passed!{_RESET} Jibsa is ready to run.")
        return 0
    else:
        failed = total - passed
        print(f"{_RED}{_BOLD}{failed} check(s) failed{_RESET} out of {total}. Fix the issues above and re-run.")
        return 1


if __name__ == "__main__":
    sys.exit(run_doctor())
