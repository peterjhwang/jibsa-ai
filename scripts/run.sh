#!/usr/bin/env bash
# scripts/run.sh — Start Jibsa (Socket Mode).
#
# Usage:
#   ./scripts/run.sh              # normal
#   LOG_LEVEL=DEBUG ./scripts/run.sh  # verbose
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -d .venv ] || { echo "No .venv found — run ./scripts/setup.sh first"; exit 1; }
[ -f .env ]  || { echo "No .env found — copy .env.example and fill in secrets"; exit 1; }

exec .venv/bin/python -m src.app "$@"
