#!/usr/bin/env bash
# scripts/test.sh — Run the test suite.
#
# Usage:
#   ./scripts/test.sh                  # all tests
#   ./scripts/test.sh -k "test_name"   # specific test
#   ./scripts/test.sh -x               # stop on first failure
#   ./scripts/test.sh --cov            # with coverage
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

[ -d .venv ] || { echo "No .venv found — run ./scripts/setup.sh first"; exit 1; }

exec .venv/bin/python -m pytest tests/ -v "$@"
