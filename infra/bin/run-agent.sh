#!/usr/bin/env bash
# Runs one tick of a service agent (triage | analyst | release | nightly | jenkins-watch). Called by its systemd timer (Linux VM) or
# launchd agent (macOS host). One run at a time per agent; pulls agent-ops and the read-only repo
# clones first; appends the JSON result to a daily log.
set -euo pipefail
AGENT="${1:?usage: run-agent.sh <triage|analyst|release|nightly|jenkins-watch>}"
case "$AGENT" in triage|analyst|release|nightly|jenkins-watch) ;; *) echo "unknown agent $AGENT" >&2; exit 1;; esac

# launchd has no EnvironmentFile: load the env file ourselves when it is given.
if [ -n "${AGENT_OPS_ENV:-}" ]; then
  [ -r "$AGENT_OPS_ENV" ] || { echo "cannot read $AGENT_OPS_ENV" >&2; exit 1; }
  set -a; . "$AGENT_OPS_ENV"; set +a
fi

# Auth: the company Console key when it is set; otherwise (dry run only) a Claude Code login token
# from `claude setup-token`. Empty values are dropped so they don't shadow the other one.
[ -n "${ANTHROPIC_API_KEY:-}" ] || unset ANTHROPIC_API_KEY
[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ] || unset CLAUDE_CODE_OAUTH_TOKEN
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then unset CLAUDE_CODE_OAUTH_TOKEN; AUTH=api_key
elif [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then AUTH=oauth_token
else AUTH=cli_login; fi

if [ "$(uname)" = Darwin ]; then
  default_logs="$HOME/Library/Logs/agent-ops"; default_locks="${TMPDIR:-/tmp}"
else
  default_logs=/var/log/agents; default_locks=/run/lock
fi
ROOT="${AGENT_OPS_ROOT:-/srv/agent-ops}"
REPOS_DIR="${AGENT_REPOS_DIR:-/srv/repos}"
LOG_DIR="${AGENT_LOG_ROOT:-$default_logs}/$AGENT"
LOCK="${AGENT_LOCK_DIR:-$default_locks}/agent-$AGENT.lock"
mkdir -p "$LOG_DIR"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Lock: a directory holding the owner's pid (flock does not exist on macOS). A lock whose pid is
# gone is stale and taken over.
if ! mkdir "$LOCK" 2>/dev/null; then
  if kill -0 "$(cat "$LOCK/pid" 2>/dev/null || echo 0)" 2>/dev/null; then
    echo "$(ts) previous $AGENT run still active, skipping" >> "$LOG_DIR/skipped.log"; exit 0
  fi
  rm -rf "$LOCK"; mkdir "$LOCK"
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

# Model and turn cap per agent (the agents share a Claude plan's usage limits, so neither runs on
# the plan's default model). Release posts the status that lets a merge reach production: keep it
# off Haiku until a side-by-side comparison says otherwise.
case "$AGENT" in
  triage)  MODEL="${AGENT_MODEL_TRIAGE:-sonnet}";  TURNS="${AGENT_MAX_TURNS_TRIAGE:-${AGENT_MAX_TURNS:-80}}";;
  # One deep analysis per run (root cause / plan with evidence): Opus, room to query and read.
  analyst) MODEL="${AGENT_MODEL_ANALYST:-opus}";   TURNS="${AGENT_MAX_TURNS_ANALYST:-150}";;
  # Assesses Jenkins failures and posts a short report to the team's Telegram topic (only when
  # infra/jenkins-watch/watch.py collect, its precheck, found something new).
  jenkins-watch) MODEL="${AGENT_MODEL_JENKINS_WATCH:-sonnet}"; TURNS="${AGENT_MAX_TURNS_JENKINS_WATCH:-60}";;
  release) MODEL="${AGENT_MODEL_RELEASE:-sonnet}"; TURNS="${AGENT_MAX_TURNS_RELEASE:-${AGENT_MAX_TURNS:-40}}";;
  nightly) MODEL="${AGENT_MODEL_NIGHTLY:-sonnet}"; TURNS="${AGENT_MAX_TURNS_NIGHTLY:-300}";;
esac

# --- Gates that run before anything costs tokens ------------------------------------------------
# State files in $LOG_DIR:
#   paused      until=<epoch> + reason=<text>; set by the circuit breaker or a Claude usage limit.
#               Ticks are skipped until then; the first tick after it is a probe run.
#   fail-count  failed runs in a row (cleared by a good run).
# Resume an agent by hand: rm "$LOG_DIR/paused" "$LOG_DIR/fail-count".
LOG_ROOT="$(dirname "$LOG_DIR")"
BREAKER_FAILS="${AGENT_BREAKER_FAILS:-3}"
BREAKER_COOLOFF_S="${AGENT_BREAKER_COOLOFF_S:-3600}"
BUDGET="${AGENT_BUDGET_5H_USD:-60}"
alert() { bash "$ROOT/infra/bin/alert.sh" "$1" >/dev/null 2>>"$LOG_DIR/alert.err" || true; }
iso() { jq -rn --argjson e "$1" '$e | todate'; }
int() { case "${1:-}" in ''|*[!0-9]*) echo 0;; *) echo "$1";; esac; }
now="$(date -u +%s)"

paused_until="$(int "$(sed -n 's/^until=//p' "$LOG_DIR/paused" 2>/dev/null)")"
paused_reason="$(sed -n 's/^reason=//p' "$LOG_DIR/paused" 2>/dev/null || true)"
if [ "$now" -lt "$paused_until" ]; then
  echo "$(ts) paused until $(iso "$paused_until"): $paused_reason" >> "$LOG_DIR/skipped.log"; exit 0
fi

# Budget: API-price cost of all service-agent runs (all agents, one shared plan) in the last 5 h,
# from our own run logs (the agents' login token can't read the plan's usage). Over budget → skip
# and alert once per 5 h. AGENT_BUDGET_5H_USD=off disables it.
if [ "$BUDGET" != off ]; then
  spent="$({ find "$LOG_ROOT/triage" "$LOG_ROOT/analyst" "$LOG_ROOT/release" "$LOG_ROOT/nightly" "$LOG_ROOT/jenkins-watch" -maxdepth 1 -name '*.jsonl' -mtime -2 2>/dev/null || true; } \
    | while read -r f; do cat "$f"; done \
    | jq -Rn --argjson since "$((now - 18000))" '[inputs | fromjson? | select(type == "object")
        | select(((.started // "") | try fromdateiso8601 catch 0) >= $since)
        | (.result.total_cost_usd // 0) | numbers] | add // 0 | . * 100 | round / 100')" || spent=0
  if awk -v s="${spent:-0}" -v b="$BUDGET" 'BEGIN { exit !(s + 0 >= b + 0) }'; then
    echo "$(ts) budget: \$$spent spent in the last 5 h (limit \$$BUDGET), skipping" >> "$LOG_DIR/skipped.log"
    last_alert="$(int "$(cat "$LOG_ROOT/budget-alerted" 2>/dev/null)")"
    if [ $(( now - last_alert )) -ge 18000 ]; then
      echo "$now" > "$LOG_ROOT/budget-alerted"
      alert "Budget reached: the service agents spent \$$spent (API prices) in the last 5 hours, limit \$$BUDGET. Ticks are skipped until older runs drop out of the 5-hour window. Raise AGENT_BUDGET_5H_USD in ~/.config/agent-ops/env if this is expected."
    fi
    exit 0
  fi
fi

if [ "${AGENT_GIT_PULL:-on}" != off ]; then
  git -C "$ROOT" pull -q --ff-only || echo "$(ts) agent-ops pull failed, running last version" >&2
  if [ "$AGENT" = "triage" ] || [ "$AGENT" = "analyst" ] || [ "$AGENT" = "nightly" ] || [ "$AGENT" = "jenkins-watch" ]; then
    # From GitLab with the agents' read-only tokens (repo-sync.sh); a failed pull keeps the last copy.
    bash "$ROOT/infra/bin/repo-sync.sh" pull "$REPOS_DIR"/*/ || echo "$(ts) repo pull failed for some of $REPOS_DIR, running on the last copy" >&2
  fi
fi

# Skip idle ticks without starting Claude (the agents share a Claude plan's usage limits).
# precheck.sh: 0 = work, 1 = idle, other = check failed → run anyway. At least one full run every
# AGENT_MAX_IDLE_HOURS (default 6) regardless, as a safety net. jenkins-watch has no such net: its
# precheck (watch.py collect) also writes the facts the agent reads, so it runs on every tick.
LAST_RUN_FILE="$LOG_DIR/last-run-at"
since="$(cat "$LAST_RUN_FILE" 2>/dev/null || echo 1970-01-01T00:00:00Z)"
max_idle_s=$(( ${AGENT_MAX_IDLE_HOURS:-6} * 3600 ))
since_epoch="$(date -j -u -f %Y-%m-%dT%H:%M:%SZ "$since" +%s 2>/dev/null || date -u -d "$since" +%s 2>/dev/null || echo 0)"
if [ "$AGENT" = jenkins-watch ]; then since_epoch="$(date -u +%s)"; fi
if [ "${AGENT_PRECHECK:-on}" != off ] && [ $(( $(date -u +%s) - since_epoch )) -lt "$max_idle_s" ]; then
  set +e; reason="$(bash "$ROOT/infra/bin/precheck.sh" "$AGENT" "$since" 2>>"$LOG_DIR/precheck.err")"; pc=$?; set -e
  if [ "$pc" -eq 1 ]; then
    echo "$(ts) idle" > "$LOG_DIR/last-idle"; exit 0
  fi
  [ "$pc" -eq 0 ] || reason="precheck failed (exit $pc), running anyway"
else
  reason="full run (no successful run in ${AGENT_MAX_IDLE_HOURS:-6} h, or precheck off)"
fi

# MCP servers for this tick: service/<agent>/.mcp.json is rendered from mcp.template.json and
# routing.yaml (code hosts; optional servers only when their settings exist). A failed render keeps
# the last file.
python3 -I "$ROOT/infra/bin/render-mcp.py" "$AGENT" 2>>"$LOG_DIR/render-mcp.err" \
  || echo "$(ts) render-mcp failed, using the last .mcp.json" >> "$LOG_DIR/render-mcp.err"
cd "$ROOT/service/$AGENT"
# MCP servers started through uvx/npx can be slow on a cold cache; the default 30 s start timeout
# made the Plane server fail on a host's first tick. install.sh also warms the caches.
export MCP_TIMEOUT="${MCP_TIMEOUT:-120000}"
# Service agents may read Claude artifacts (the team's earlier write-ups) but never publish, edit or
# delete one (guard.sh rule 8). The environment can only tighten the guard.
export AGENT_ARTIFACTS_READ_ONLY=1
# Action journal (infra/journal): the journal.sh hook reports every write of this run to agent-mcp
# on this host, as this agent and this run, authenticated with the agent's own task-board bot token.
RUN_ID="$AGENT-$(date -u +%Y%m%dT%H%M%S)-$$"
export AGENT_NAME="$AGENT" AGENT_RUN_ID="$RUN_ID" AGENT_JOURNAL_CLIENT=service
export AGENT_JOURNAL_URL="${AGENT_JOURNAL_URL:-http://127.0.0.1:${AGENT_MCP_PORT:-8765}/journal}"
case "$AGENT" in
  triage|analyst|jenkins-watch) AGENT_JOURNAL_TOKEN="${TRACKER_TRIAGE_TOKEN:-}";;
  release)        AGENT_JOURNAL_TOKEN="${TRACKER_RELEASE_TOKEN:-}";;
  nightly)        AGENT_JOURNAL_TOKEN="${TRACKER_NIGHTLY_TOKEN:-}";;
esac
export AGENT_JOURNAL_TOKEN
# Tell the agent when its last successful run started, so "since the last successful run" in
# tick.md has a concrete time (the agent has no memory between ticks).
if [ "$since" = 1970-01-01T00:00:00Z ]; then
  context='Context: no previous successful run recorded: check the last 7 days.'
else
  context="Context: this agent's last successful run started at $since (UTC). Treat \"since the last successful run\" as since that time."
fi
if [ -n "${PRECHECK_RULES_SINCE:-}" ]; then
  context="$context
PRECHECK_RULES_SINCE=$PRECHECK_RULES_SINCE (review rules: state changes before this time never count)."
fi
prompt="$(cat tick.md)

$context${AGENT_EXTRA_CONTEXT:+
$AGENT_EXTRA_CONTEXT}"
started="$(ts)"
set +e
claude -p "$prompt" \
  --model "$MODEL" \
  --strict-mcp-config --mcp-config .mcp.json \
  --max-turns "$TURNS" \
  --output-format json \
  > "$LOG_DIR/last.json" 2> "$LOG_DIR/last.err"
rc=$?
set -e

# Outcome: ok | failed (non-zero exit or an error result) | error_reported (the agent could not
# read a source and said so: summary starts with "ERROR:") | usage_limit (Claude's plan limit).
is_error="$(jq -r '.is_error // false' "$LOG_DIR/last.json" 2>/dev/null || echo false)"
api_status="$(jq -r '.api_error_status // ""' "$LOG_DIR/last.json" 2>/dev/null || true)"
result_text="$(jq -r '.result // ""' "$LOG_DIR/last.json" 2>/dev/null || true)"
outcome=ok
if [ "$rc" -ne 0 ] || [ "$is_error" = true ]; then
  outcome=failed
  if [ "$api_status" = 429 ] || printf '%s\n%s' "$result_text" "$(tail -c 2000 "$LOG_DIR/last.err" 2>/dev/null)" \
      | grep -Eiq 'usage limit|limit reached|hit your limit|out of (extra )?usage'; then
    outcome=usage_limit
  fi
elif printf '%s' "$result_text" | sed -E 's/^[[:space:]*_#>]+//' | head -c 6 | grep -q '^ERROR:'; then
  outcome=error_reported
fi
first_line() { printf '%s' "$1" | sed -E 's/^[[:space:]*_#>]+//' | sed -n '/[^[:space:]]/{p;q;}' | cut -c1-200; }
why="$(first_line "$result_text")"; [ -n "$why" ] || why="$(first_line "$(tail -n 5 "$LOG_DIR/last.err" 2>/dev/null)")"
[ -n "$why" ] || why="exit $rc, no output"

fails="$(int "$(cat "$LOG_DIR/fail-count" 2>/dev/null)")"
case "$outcome" in
  ok)
    echo "$started" > "$LAST_RUN_FILE"
    if [ "$fails" -ge "$BREAKER_FAILS" ]; then
      alert "$AGENT agent recovered: a probe run worked after $fails failed runs in a row. Ticks run normally again."
    fi
    rm -f "$LOG_DIR/fail-count" "$LOG_DIR/paused";;
  usage_limit)
    # Old CLIs print "…usage limit reached|<reset epoch>"; otherwise wait an hour and probe again.
    reset="$(int "$(printf '%s\n%s' "$result_text" "$(tail -c 2000 "$LOG_DIR/last.err" 2>/dev/null)" \
      | grep -Eo '\|[0-9]{10}' | head -1 | tr -d '|')")"
    now="$(date -u +%s)"
    if [ "$reset" -le "$now" ] || [ "$reset" -gt $(( now + 8 * 86400 )) ]; then reset=$(( now + 3600 )); fi
    printf 'until=%s\nreason=Claude usage limit\n' "$reset" > "$LOG_DIR/paused"
    if [ "$paused_reason" != "Claude usage limit" ]; then
      alert "$AGENT agent paused until $(iso "$reset"): the Claude plan's usage limit was hit. It probes again then. Message: $why"
    fi;;
  failed|error_reported)
    fails=$(( fails + 1 )); echo "$fails" > "$LOG_DIR/fail-count"
    if [ "$fails" -ge "$BREAKER_FAILS" ]; then
      until_s=$(( $(date -u +%s) + BREAKER_COOLOFF_S ))
      printf 'until=%s\nreason=%s failed runs in a row\n' "$until_s" "$fails" > "$LOG_DIR/paused"
      if [ "$fails" -eq "$BREAKER_FAILS" ]; then
        alert "$AGENT agent paused after $fails failed runs in a row. One probe run at $(iso "$until_s"), then every hour until one works. Last error: $why. Logs: $LOG_DIR. Resume by hand: rm $LOG_DIR/paused $LOG_DIR/fail-count"
      fi
    fi;;
esac

# Quality (analyst): how much of the analysis it just wrote cites a source (infra/quality/claims.py,
# measured on the item itself, not on what the agent says), and the fresh checker's verdict from its
# summary ("check: 12/14 supported, fixed 2" | "check: not run"). Never fails the run.
quality=null
if [ "$AGENT" = analyst ] && [ "$outcome" = ok ]; then
  item="$(first_line "$result_text" | grep -Eo '^[A-Z]+-[0-9]+' || true)"
  if [ -n "$item" ]; then
    claims_cmd=(python3 -I "$ROOT/infra/quality/claims.py")
    [ -z "${AGENT_CLAIMS_CMD:-}" ] || claims_cmd=("$AGENT_CLAIMS_CMD")
    measured="$("${claims_cmd[@]}" --item "$item" 2>/dev/null || true)"
    printf '%s' "$measured" | jq -e 'type == "object"' >/dev/null 2>&1 || measured=null
    check="$(printf '%s' "$result_text" | grep -Eo 'check: ([0-9]+/[0-9]+ supported(, fixed [0-9]+)?|not run)' | head -1 || true)"
    quality="$(jq -cn --arg item "$item" --arg check "$check" --argjson m "$measured" '
      {item: $item, claims: ($m.claims // null), unsourced: ($m.unsourced // null), rate: ($m.rate // null),
       check: (if $check == "" then null elif ($check | test("not run")) then "not run"
               else ($check | capture("check: (?<s>[0-9]+)/(?<t>[0-9]+) supported(, fixed (?<f>[0-9]+))?")
                     | {supported: (.s | tonumber), total: (.t | tonumber), fixed: ((.f // "0") | tonumber)}) end)}' 2>/dev/null || echo null)"
  fi
fi

{ printf '{"agent":"%s","run_id":"%s","auth":"%s","model":"%s","reason":%s,"started":"%s","finished":"%s","exit":%d,"outcome":"%s","quality":%s,"result":' "$AGENT" "$RUN_ID" "$AUTH" "$MODEL" "$(jq -Rn --arg r "${reason:-}" '$r')" "$started" "$(ts)" "$rc" "$outcome" "${quality:-null}"
  r="$(jq -c . "$LOG_DIR/last.json" 2>/dev/null | tr -d '\n')"; printf '%s' "${r:-null}"
  printf '}\n'; } >> "$LOG_DIR/$(date +%F).jsonl"
exit "$rc"
