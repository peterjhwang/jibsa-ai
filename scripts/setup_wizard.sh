#!/usr/bin/env bash
# scripts/setup_wizard.sh — Interactive setup wizard for first-time configuration.
#
# Usage:
#   ./scripts/setup_wizard.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Use .venv python if available, else system python3
if [ -f .venv/bin/python ]; then
    exec .venv/bin/python -m src.setup "$@"
else
    exec python3 -m src.setup "$@"
fi
