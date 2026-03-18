#!/usr/bin/env bash
# scripts/run.sh — Start Jibsa (Socket Mode) in the background.
#
# Usage:
#   ./scripts/run.sh              # start in background
#   LOG_LEVEL=DEBUG ./scripts/run.sh  # verbose
#   ./scripts/run.sh stop         # stop the running instance
#   ./scripts/run.sh status       # check if running
#   ./scripts/run.sh logs         # tail the log file
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PID_FILE="$ROOT/jibsa.pid"
LOG_FILE="$ROOT/jibsa.log"

case "${1:-}" in
  stop)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      kill "$(cat "$PID_FILE")"
      rm -f "$PID_FILE"
      echo "Jibsa stopped."
    else
      echo "Jibsa is not running."
    fi
    exit 0
    ;;
  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Jibsa is running (PID $(cat "$PID_FILE"))."
    else
      echo "Jibsa is not running."
    fi
    exit 0
    ;;
  logs)
    exec tail -f "$LOG_FILE"
    ;;
esac

[ -d .venv ] || { echo "No .venv found — run ./scripts/setup.sh first"; exit 1; }
[ -f .env ]  || { echo "No .env found — copy .env.example and fill in secrets"; exit 1; }

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Jibsa is already running (PID $(cat "$PID_FILE"))."
  exit 1
fi

nohup .venv/bin/python -m src.app >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "Jibsa started (PID $!). Logs: $LOG_FILE"
