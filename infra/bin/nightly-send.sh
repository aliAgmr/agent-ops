#!/usr/bin/env bash
# Sends today's nightly messages (infra/nightly/send.py) on the morning schedule set up by SETUP.md
# (launchd or systemd): the summaries to each project's group chat (or its lead's DM), the full list to
# the question owner. Nothing to send (no pass tonight, or it failed) → one alert instead.
set -euo pipefail
if [ -n "${AGENT_OPS_ENV:-}" ]; then
  [ -r "$AGENT_OPS_ENV" ] || { echo "cannot read $AGENT_OPS_ENV" >&2; exit 1; }
  set -a; . "$AGENT_OPS_ENV"; set +a
fi
ROOT="${AGENT_OPS_ROOT:-/srv/agent-ops}"
LOG_DIR="${AGENT_LOG_ROOT:-$HOME/Library/Logs/agent-ops}/nightly"
mkdir -p "$LOG_DIR"
day="$(TZ="${NIGHTLY_TZ:-${TZ:-}}" date +%F)"
RUN_DIR="${NIGHTLY_RUN_ROOT:-/srv/nightly}/$day"
if [ ! -s "$RUN_DIR/dm.en.txt" ]; then
  bash "$ROOT/infra/bin/alert.sh" "No nightly report for $day to send (the pass didn't finish). Logs: $LOG_DIR." >/dev/null 2>&1 || true
  exit 1
fi
uv run -q --script "$ROOT/infra/nightly/send.py" "$RUN_DIR" "$@" >> "$LOG_DIR/sends.log" 2>> "$LOG_DIR/error.log"
