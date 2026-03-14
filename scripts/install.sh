#!/usr/bin/env bash
# scripts/install.sh — Install pinned dependencies into .venv.
#
# Usage:
#   ./scripts/install.sh          # install production deps
#   ./scripts/install.sh --dev    # install production + dev deps
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -d .venv ] || { echo "No .venv found — run ./scripts/setup.sh first"; exit 1; }

printf '\033[1;34m▸ Installing dependencies from requirements.txt\033[0m\n'
uv pip install -r requirements.txt

if [[ "${1:-}" == "--dev" ]]; then
    if [ -f requirements-dev.txt ]; then
        printf '\033[1;34m▸ Installing dev dependencies\033[0m\n'
        uv pip install -r requirements-dev.txt
    else
        printf '\033[1;34m▸ Installing pytest\033[0m\n'
        uv pip install pytest
    fi
fi

printf '\033[1;32m✔ Installed.\033[0m\n'
