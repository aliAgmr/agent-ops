#!/usr/bin/env bash
# One nightly pass (infra/nightly/README.md). Called by launchd or systemd (the nightly schedule set
# up by SETUP.md; default 02:30 local, Monday–Friday). Steps, all into RUN_DIR=/srv/nightly/<date>
# (the date in NIGHTLY_TZ, default the host's time zone):
#   1. collect.py → facts.json   (task board + code hosts since the last good run; failure stops the pass)
#   2. checks.py  → checks.json  (tests in Docker, osv-scanner, gitleaks, semgrep, nightly E2E)
#   2b. traps     → traps.json   (warehouse-trap checks, read-only ClickHouse) and
#       lineage check → lineage.json (rebuilds the lineage cache; hand-file names that no longer exist)
#   3. run-agent.sh nightly      (Claude: updates the board, writes report.md, group-*.en.txt, dm.en.txt)
#   4. publish.py → page.json    (the report page on the board)
# The messages go out later in the morning (nightly-send.sh), after quiet hours.
#   bash infra/bin/nightly.sh [--since <iso>] [--skip-tests] [--no-agent] [--dry-run]
# --dry-run: the board is read-only for the agent (the guard enforces AGENT_TRACKER_READ_ONLY=1), no page
# is published, nothing is sent, and the files go to /srv/nightly/dry-<date> with their own logs, so
# the real pass's window and state are untouched.
set -euo pipefail
if [ -n "${AGENT_OPS_ENV:-}" ]; then
  [ -r "$AGENT_OPS_ENV" ] || { echo "cannot read $AGENT_OPS_ENV" >&2; exit 1; }
  set -a; . "$AGENT_OPS_ENV"; set +a
fi
ROOT="${AGENT_OPS_ROOT:-/srv/agent-ops}"
LOG_DIR="${AGENT_LOG_ROOT:-$HOME/Library/Logs/agent-ops}/nightly"
mkdir -p "$LOG_DIR"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
alert() { bash "$ROOT/infra/bin/alert.sh" "$1" >/dev/null 2>&1 || true; }

since="" ; skip_tests=""; no_agent=""; dry=""
while [ $# -gt 0 ]; do
  case "$1" in
    --since) since="$2"; shift 2;;
    --skip-tests) skip_tests=--skip-tests; shift;;
    --no-agent) no_agent=1; shift;;
    --dry-run) dry=1; shift;;
    *) echo "unknown option $1" >&2; exit 1;;
  esac
done

# The nightly bot's task-board token: TRACKER_NIGHTLY_TOKEN, or a file (TRACKER_NIGHTLY_TOKEN_FILE).
if [ -z "${TRACKER_NIGHTLY_TOKEN:-}" ]; then
  TOKEN_FILE="${TRACKER_NIGHTLY_TOKEN_FILE:-$HOME/.config/agent-ops/tracker-nightly.token}"
  [ -s "$TOKEN_FILE" ] || { echo "$(ts) no task-board token for the nightly bot (TRACKER_NIGHTLY_TOKEN or $TOKEN_FILE)" >> "$LOG_DIR/error.log"; exit 1; }
  TRACKER_NIGHTLY_TOKEN="$(tr -d '\n' < "$TOKEN_FILE")"
fi
export TRACKER_NIGHTLY_TOKEN

# Window: since the last good pass (agent run-agent.sh writes last-run-at), else 24 h.
[ -n "$since" ] || since="$(cat "$LOG_DIR/last-run-at" 2>/dev/null || true)"
[ -n "$since" ] || since="$(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)"
day="$(TZ="${NIGHTLY_TZ:-${TZ:-}}" date +%F)"
RUN_DIR="${NIGHTLY_RUN_ROOT:-/srv/nightly}/${dry:+dry-}$day"
mkdir -p "$RUN_DIR"; chmod 700 "$(dirname "$RUN_DIR")" "$RUN_DIR"
echo "$(ts) pass $day since $since → $RUN_DIR" >> "$LOG_DIR/passes.log"

git -C "$ROOT" pull -q --ff-only 2>/dev/null || true
if ! uv run -q --script "$ROOT/infra/nightly/collect.py" --since "$since" --out "$RUN_DIR/facts.json" >> "$LOG_DIR/passes.log" 2>> "$LOG_DIR/error.log"; then
  alert "Nightly pass $day stopped: collecting task-board/code-host facts failed. See $LOG_DIR/error.log."
  exit 1
fi
if ! uv run -q --script "$ROOT/infra/nightly/checks.py" --since "$since" --out "$RUN_DIR/checks.json" $skip_tests >> "$LOG_DIR/passes.log" 2>> "$LOG_DIR/error.log"; then
  echo '{"error": "checks.py failed; see the nightly error.log"}' > "$RUN_DIR/checks.json"
fi
bash "$ROOT/infra/bin/traps" run > "$RUN_DIR/traps.json" 2>> "$LOG_DIR/error.log" \
  || echo '{"error": "traps run failed; see the nightly error.log"}' > "$RUN_DIR/traps.json"
bash "$ROOT/infra/bin/lineage" check > "$RUN_DIR/lineage.json" 2>> "$LOG_DIR/error.log" \
  || [ -s "$RUN_DIR/lineage.json" ] || echo '{"error": "lineage check failed; see the nightly error.log"}' > "$RUN_DIR/lineage.json"
[ -n "$no_agent" ] && { echo "$(ts) --no-agent: stopping before the agent" >> "$LOG_DIR/passes.log"; exit 0; }

extra="RUN_DIR=$RUN_DIR
SINCE=$since
Today: $day (${NIGHTLY_TZ:-host time zone}).
Board: ${TRACKER_BASE_URL:-}, workspace ${TRACKER_WORKSPACE:-}."
set +e
if [ -n "$dry" ]; then
  AGENT_TRACKER_READ_ONLY=1 AGENT_PLANE_READ_ONLY=1 AGENT_LOG_ROOT="$RUN_DIR/logs" AGENT_BUDGET_5H_USD=off AGENT_PRECHECK=off \
    AGENT_EXTRA_CONTEXT="$extra
DRY_RUN=true: change nothing on the task board (writes are blocked). Do every step as if live, and record each change you would make in actions.json with \"dry_run\": true; write the report and messages saying what you would do." \
    bash "$ROOT/infra/bin/run-agent.sh" nightly
else
  AGENT_PRECHECK=off AGENT_EXTRA_CONTEXT="$extra" bash "$ROOT/infra/bin/run-agent.sh" nightly
fi
rc=$?
set -e
[ "$rc" -eq 0 ] || { echo "$(ts) agent run failed (exit $rc)" >> "$LOG_DIR/error.log"; exit "$rc"; }
[ -n "$dry" ] && { echo "$(ts) dry run $day done: $RUN_DIR" >> "$LOG_DIR/passes.log"; exit 0; }
uv run -q --script "$ROOT/infra/nightly/publish.py" "$RUN_DIR" >> "$LOG_DIR/passes.log" 2>> "$LOG_DIR/error.log" || true
echo "$(ts) pass $day done" >> "$LOG_DIR/passes.log"
