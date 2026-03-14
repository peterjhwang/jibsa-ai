#!/usr/bin/env bash
# scripts/setup.sh — Bootstrap the dev environment from scratch.
#
# Installs uv (if missing), creates .venv, compiles requirements, and installs everything.
#
# Usage:
#   ./scripts/setup.sh          # full setup
#   ./scripts/setup.sh --no-uv  # skip uv install (already installed)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON_VERSION="3.12"

# ── helpers ──────────────────────────────────────────────────────────────────
info()  { printf '\033[1;34m▸ %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
fail()  { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. Install uv ───────────────────────────────────────────────────────────
if [[ "${1:-}" != "--no-uv" ]]; then
    if command -v uv &>/dev/null; then
        ok "uv $(uv --version | head -1) already installed"
    else
        info "Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
        ok "uv installed: $(uv --version | head -1)"
    fi
fi

# ── 2. Create .venv ─────────────────────────────────────────────────────────
if [ ! -d .venv ]; then
    info "Creating .venv (Python $PYTHON_VERSION)..."
    uv venv --python "$PYTHON_VERSION"
    ok ".venv created"
else
    ok ".venv already exists"
fi

# ── 3. Compile requirements.in → requirements.txt ───────────────────────────
info "Compiling requirements.in → requirements.txt..."
uv pip compile requirements.in -o requirements.txt --quiet
ok "requirements.txt compiled (pinned)"

# ── 4. Install dependencies ─────────────────────────────────────────────────
info "Installing dependencies..."
uv pip install -r requirements.txt --quiet
ok "Dependencies installed"

# ── 5. Install dev dependencies ─────────────────────────────────────────────
if [ -f requirements-dev.in ]; then
    info "Compiling requirements-dev.in → requirements-dev.txt..."
    uv pip compile requirements-dev.in -o requirements-dev.txt --quiet
    uv pip install -r requirements-dev.txt --quiet
    ok "Dev dependencies installed"
else
    info "Installing pytest (dev)..."
    uv pip install pytest --quiet
    ok "pytest installed"
fi

# ── 6. Copy .env if missing ─────────────────────────────────────────────────
if [ ! -f .env ] && [ -f .env.example ]; then
    cp .env.example .env
    info "Copied .env.example → .env  (fill in your secrets)"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
ok "Setup complete! Next steps:"
echo "   1. Fill in .env with your API keys"
echo "   2. ./scripts/run.sh        — start Jibsa"
echo "   3. ./scripts/test.sh       — run tests"
echo "   4. ./scripts/doctor.sh     — check configuration"
