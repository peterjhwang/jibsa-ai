#!/usr/bin/env bash
# scripts/compile.sh — Compile requirements.in → requirements.txt (pinned).
#
# Run this after editing requirements.in to lock dependency versions.
#
# Usage:
#   ./scripts/compile.sh            # compile
#   ./scripts/compile.sh --upgrade  # compile with latest versions
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

EXTRA_ARGS=("$@")

printf '\033[1;34m▸ Compiling requirements.in → requirements.txt\033[0m\n'
uv pip compile requirements.in -o requirements.txt "${EXTRA_ARGS[@]}"

if [ -f requirements-dev.in ]; then
    printf '\033[1;34m▸ Compiling requirements-dev.in → requirements-dev.txt\033[0m\n'
    uv pip compile requirements-dev.in -o requirements-dev.txt "${EXTRA_ARGS[@]}"
fi

printf '\033[1;32m✔ Done. Run ./scripts/install.sh to install.\033[0m\n'
