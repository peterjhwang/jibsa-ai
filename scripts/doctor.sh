#!/usr/bin/env bash
# scripts/doctor.sh — Health check: verify env, deps, and config.
#
# Usage:
#   ./scripts/doctor.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PASS=0
WARN=0
FAIL=0

check() { printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; PASS=$((PASS+1)); }
warn()  { printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; WARN=$((WARN+1)); }
fail()  { printf '\033[1;31m  ✖ %s\033[0m\n' "$*"; FAIL=$((FAIL+1)); }

echo ""
printf '\033[1m🩺 Jibsa Doctor\033[0m\n\n'

# ── Python & uv ─────────────────────────────────────────────────────────────
printf '\033[1m[Runtime]\033[0m\n'
if command -v uv &>/dev/null; then
    check "uv $(uv --version 2>/dev/null | head -1)"
else
    fail "uv not found — install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

if [ -d .venv ]; then
    PY_VER=$(.venv/bin/python --version 2>/dev/null || echo "unknown")
    check ".venv exists ($PY_VER)"
else
    fail ".venv not found — run: ./scripts/setup.sh"
fi

# ── Dependencies ─────────────────────────────────────────────────────────────
printf '\n\033[1m[Dependencies]\033[0m\n'
if [ -f requirements.txt ]; then
    check "requirements.txt exists"
else
    fail "requirements.txt missing — run: ./scripts/compile.sh"
fi

if [ -f requirements.in ]; then
    check "requirements.in exists"
else
    warn "requirements.in missing — using requirements.txt directly"
fi

if [ -d .venv ]; then
    MISSING=$(.venv/bin/python -c "
import importlib, sys
deps = ['slack_bolt', 'yaml', 'dotenv', 'pydantic', 'notion_client', 'crewai', 'duckduckgo_search', 'apscheduler', 'tenacity']
missing = []
for d in deps:
    try:
        importlib.import_module(d)
    except ImportError:
        missing.append(d)
if missing:
    print(','.join(missing))
" 2>/dev/null || echo "CHECK_FAILED")

    if [ -z "$MISSING" ]; then
        check "All core packages importable"
    elif [ "$MISSING" = "CHECK_FAILED" ]; then
        warn "Could not verify imports"
    else
        fail "Missing packages: $MISSING — run: ./scripts/install.sh"
    fi
fi

# ── Environment variables ────────────────────────────────────────────────────
printf '\n\033[1m[Environment]\033[0m\n'

# Source .env for checking (don't export)
if [ -f .env ]; then
    check ".env file exists"
    # shellcheck disable=SC1091
    set -a; source .env 2>/dev/null; set +a
else
    fail ".env missing — copy .env.example and fill in secrets"
fi

_check_var() {
    local var="$1" required="${2:-true}" desc="${3:-}"
    local val="${!var:-}"
    if [ -n "$val" ] && [ "$val" != "${desc}" ]; then
        # Mask the value
        check "$var is set (${val:0:8}...)"
    elif [ "$required" = "true" ]; then
        fail "$var is not set${desc:+ — $desc}"
    else
        warn "$var is not set${desc:+ — $desc}"
    fi
}

_check_var SLACK_BOT_TOKEN true
_check_var SLACK_APP_TOKEN true

# Check LLM provider key based on settings.yaml
PROVIDER=$(python3 -c "
import yaml
with open('config/settings.yaml') as f:
    c = yaml.safe_load(f)
print(c.get('llm',{}).get('provider','anthropic'))
" 2>/dev/null || echo "anthropic")

case "$PROVIDER" in
    anthropic) _check_var ANTHROPIC_API_KEY true ;;
    openai)    _check_var OPENAI_API_KEY true ;;
    google)    _check_var GOOGLE_API_KEY true ;;
esac

_check_var NOTION_TOKEN false "needed for Notion integration"
_check_var GOOGLE_API_KEY false "needed for image generation"
_check_var ZENROWS_API_KEY false "needed for web reader + search fallback"

# ── Config ───────────────────────────────────────────────────────────────────
printf '\n\033[1m[Config]\033[0m\n'
if [ -f config/settings.yaml ]; then
    check "config/settings.yaml exists"
    # Validate via pydantic
    if [ -d .venv ]; then
        VALID=$(.venv/bin/python -c "
from src.config_schema import validate_config
import yaml
with open('config/settings.yaml') as f:
    validate_config(yaml.safe_load(f))
print('ok')
" 2>&1 || true)
        if [ "$VALID" = "ok" ]; then
            check "settings.yaml passes validation"
        else
            fail "settings.yaml validation failed: $VALID"
        fi
    fi
else
    fail "config/settings.yaml missing"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
printf '\033[1mSummary: %d passed, %d warnings, %d failed\033[0m\n' "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    echo ""
    printf '\033[1;31mFix the failures above before running Jibsa.\033[0m\n'
    exit 1
fi
echo ""
