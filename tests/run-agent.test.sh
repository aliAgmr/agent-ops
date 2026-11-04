#!/usr/bin/env bash
# Tests for the cost and safety gates in infra/bin/run-agent.sh (model/turns, budget, usage-limit
# pause, circuit breaker, alerts), offline: a fake `claude` and a fake `curl` first on PATH.
# Run: bash tests/run-agent.test.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export AGENT_JOURNAL_DB="$T/journal.sqlite3"   # never the real action journal
mkdir -p "$T/bin"

# fake claude: behaviour from FAKE_MODE (ok | fail | error | limit), cost from FAKE_COST
cat >"$T/bin/claude" <<'EOF'
#!/usr/bin/env bash
printf "%s\n" "$(echo "$*" | tr "\n" " ")" >> "$FAKE_DIR/claude-calls"
echo "artifacts_ro=${AGENT_ARTIFACTS_READ_ONLY:-} cwd=$(basename "$PWD")" >> "$FAKE_DIR/claude-env"
echo "journal name=${AGENT_NAME:-} run=${AGENT_RUN_ID:-} url=${AGENT_JOURNAL_URL:-} token=${AGENT_JOURNAL_TOKEN:+set}" >> "$FAKE_DIR/claude-journal"
cost="${FAKE_COST:-0.01}"
case "${FAKE_MODE:-ok}" in
  ok)    echo "{\"type\":\"result\",\"is_error\":false,\"result\":\"${FAKE_RESULT:-nothing to do}\",\"total_cost_usd\":$cost}";;
  fail)  echo "{\"type\":\"result\",\"is_error\":true,\"result\":\"MCP server plane failed to start token=abcdefghijklmnopqrstuvwxyz0123456789ABCD\",\"total_cost_usd\":$cost}"; exit 1;;
  error) echo "{\"type\":\"result\",\"is_error\":false,\"result\":\"**ERROR:** could not list Plane work items (502)\",\"total_cost_usd\":$cost}";;
  limit) echo "{\"type\":\"result\",\"is_error\":true,\"result\":\"Claude AI usage limit reached|$FAKE_RESET\",\"total_cost_usd\":0}"; exit 1;;
esac
EOF
# fake curl: records the Telegram message text and the whole command line (to check the bot token
# never appears in argv)
cat >"$T/bin/curl" <<'EOF'
#!/usr/bin/env bash
cfg="$(cat)"; echo "$*" >> "$FAKE_DIR/curl-argv"
case "$cfg" in *api.telegram.org*) ;; *) exit 22;; esac
while [ $# -gt 0 ]; do case "$1" in --data-urlencode) case "$2" in text=*) echo "${2#text=}" >> "$FAKE_DIR/telegram";; esac; shift;; esac; shift; done
EOF
chmod +x "$T/bin/claude" "$T/bin/curl"

export PATH="$T/bin:$PATH" FAKE_DIR="$T"
unset AGENT_OPS_ENV ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN AGENT_MAX_TURNS AGENT_MODEL_TRIAGE \
  AGENT_MODEL_RELEASE AGENT_MODEL_ANALYST AGENT_MAX_TURNS_ANALYST AGENT_ARTIFACTS_READ_ONLY AGENT_BUDGET_5H_USD AGENT_BREAKER_FAILS AGENT_ALERTS
export AGENT_OPS_ROOT="$ROOT" AGENT_LOG_ROOT="$T/logs" AGENT_LOCK_DIR="$T/locks" \
  AGENT_GIT_PULL=off AGENT_PRECHECK=off TELEGRAM_BOT_TOKEN="bot-secret-123:XYZ" ALERT_CHAT_ID=42
mkdir -p "$T/locks"
L="$T/logs/release"
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
reset() { rm -rf "$T/logs" "$T/claude-calls" "$T/claude-env" "$T/telegram" "$T/curl-argv"; unset FAKE_MODE FAKE_COST; }
run() { FAKE_MODE="${1:-ok}" bash "$ROOT/infra/bin/run-agent.sh" "${2:-release}" >/dev/null 2>&1; }
calls() { wc -l < "$T/claude-calls" 2>/dev/null | tr -d ' ' || echo 0; }
alerts() { [ -f "$T/telegram" ] && wc -l < "$T/telegram" | tr -d ' ' || echo 0; }
lastj() { tail -1 "$L/$(date +%F).jsonl"; }

# 1. model and turn cap per agent; a good run is logged with model + outcome
reset; run ok release
check "release runs on sonnet, 40 turns" 'grep -q -- "--model sonnet" "$T/claude-calls" && grep -q -- "--max-turns 40" "$T/claude-calls"'
check "jsonl has model and outcome" '[ "$(lastj | jq -r "[.model, .outcome] | join(\" \")")" = "sonnet ok" ]'
check "good run records last-run-at" '[ -s "$L/last-run-at" ]'
reset; run ok triage
check "triage runs on sonnet, 80 turns" 'grep -q -- "--model sonnet --strict-mcp-config --mcp-config .mcp.json --max-turns 80" "$T/claude-calls"'
check "service agents read artifacts read-only" 'grep -q "^artifacts_ro=1 cwd=triage$" "$T/claude-env"'
check "journal: the run's writes are reported as this agent and run" 'grep -Eq "^journal name=triage run=triage-[0-9T]+-[0-9]+ url=http://127.0.0.1:8765/journal" "$T/claude-journal"'
check "journal: the run id is in the run log" 'tail -1 "$T/logs/triage/$(date +%F).jsonl" | jq -e ".run_id | startswith(\"triage-\")" >/dev/null'
reset; run ok analyst
# quality: the analyst's run log carries the claim count of the item it analysed and the checker's verdict
cat >"$T/bin/fake-claims" <<'EOS'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_DIR/claims-calls"; echo '{"claims": 14, "unsourced": 2, "rate": 0.143, "examples": []}'
EOS
chmod +x "$T/bin/fake-claims"
reset; AGENT_CLAIMS_CMD="$T/bin/fake-claims" FAKE_RESULT="BIL-12 → bug: double count; options 2; check: 12/14 supported, fixed 2" run ok analyst
check "quality: claims measured on the analysed item" 'grep -q -- "--item BIL-12" "$T/claims-calls"'
check "quality: in the run log with the checker verdict" '[ "$(tail -1 "$T/logs/analyst/$(date +%F).jsonl" | jq -c "[.quality.item, .quality.claims, .quality.unsourced, .quality.check.supported, .quality.check.total, .quality.check.fixed]")" = "[\"BIL-12\",14,2,12,14,2]" ]'
reset; AGENT_CLAIMS_CMD="$T/bin/fake-claims" FAKE_RESULT="BIL-13 → feature: x; check: not run" run ok analyst
check "quality: a check that didn't run says so" '[ "$(tail -1 "$T/logs/analyst/$(date +%F).jsonl" | jq -r .quality.check)" = "not run" ]'
reset; AGENT_CLAIMS_CMD=/nonexistent FAKE_RESULT="BIL-14 → bug: y" run ok analyst
check "quality: a failed measure never fails the run" '[ "$(tail -1 "$T/logs/analyst/$(date +%F).jsonl" | jq -c "[.outcome, .quality.item, .quality.claims]")" = "[\"ok\",\"BIL-14\",null]" ]'
reset; FAKE_RESULT="nothing to do" run ok analyst
check "quality: none when no item was analysed" '[ "$(tail -1 "$T/logs/analyst/$(date +%F).jsonl" | jq -c .quality)" = "null" ]'
check "analyst runs on opus, 150 turns, in service/analyst" 'grep -q -- "--model opus --strict-mcp-config --mcp-config .mcp.json --max-turns 150" "$T/claude-calls" && grep -q "^artifacts_ro=1 cwd=analyst$" "$T/claude-env"'
reset; AGENT_MODEL_RELEASE=haiku AGENT_MAX_TURNS_RELEASE=20 run ok release
check "model and turns can be overridden" 'grep -q -- "--model haiku" "$T/claude-calls" && grep -q -- "--max-turns 20" "$T/claude-calls"'

# 2. breaker: 3 failures in a row pause the agent, one alert
reset; run fail; run fail
check "2 failures: not paused, no alert" '[ ! -f "$L/paused" ] && [ "$(alerts)" = 0 ] && [ "$(cat "$L/fail-count")" = 2 ]'
run fail
check "3rd failure pauses" 'grep -q "^reason=3 failed runs in a row" "$L/paused"'
check "one alert on pause" '[ "$(alerts)" = 1 ] && grep -q "release agent paused after 3 failed runs" "$T/telegram"'
check "alert redacts token-like text" '! grep -q abcdefghijklmnopqrstuvwxyz0123456789 "$T/telegram" && grep -q "\[redacted\]" "$T/telegram"'
check "bot token never in curl argv" '! grep -q "bot-secret-123" "$T/curl-argv"'
check "failed run doesn't record last-run-at" '[ ! -f "$L/last-run-at" ]'
run ok
check "paused: tick skipped without calling claude" '[ "$(calls)" = 3 ] && grep -q "paused until" "$L/skipped.log"'

# 3. cool-off over, probe fails: paused again, no second alert
printf 'until=1\nreason=3 failed runs in a row\n' > "$L/paused"
run fail
check "failed probe re-pauses, no new alert" '[ "$(cat "$L/fail-count")" = 4 ] && [ "$(alerts)" = 1 ] && [ "$(sed -n "s/^until=//p" "$L/paused")" -gt "$(date -u +%s)" ]'

# 4. cool-off over, probe works: recovered alert, state cleared
printf 'until=1\nreason=4 failed runs in a row\n' > "$L/paused"
run ok
check "good probe clears breaker" '[ ! -f "$L/paused" ] && [ ! -f "$L/fail-count" ]'
check "recovery alert" '[ "$(alerts)" = 2 ] && grep -q "release agent recovered" "$T/telegram"'

# 5. "ERROR:" summary counts as a failure (even with exit 0)
reset; run error
check "ERROR: summary is a failure" '[ "$(cat "$L/fail-count")" = 1 ] && [ ! -f "$L/last-run-at" ] && [ "$(lastj | jq -r .outcome)" = error_reported ]'
run ok
check "a good run resets the count" '[ ! -f "$L/fail-count" ]'

# 6. usage limit: pause until the reset time, one alert, not a breaker failure
reset; export FAKE_RESET=$(( $(date -u +%s) + 7200 ))
run limit
check "usage limit pauses until reset" '[ "$(sed -n "s/^until=//p" "$L/paused")" = "$FAKE_RESET" ] && grep -q "^reason=Claude usage limit" "$L/paused"'
check "usage limit: one alert, no fail-count" '[ "$(alerts)" = 1 ] && grep -q "usage limit was hit" "$T/telegram" && [ ! -f "$L/fail-count" ]'
printf 'until=1\nreason=Claude usage limit\n' > "$L/paused"
run limit
check "still limited on probe: no second alert" '[ "$(alerts)" = 1 ] && [ -f "$L/paused" ]'
unset FAKE_RESET

# 7. budget: spend in the last 5 h over the limit skips the tick, one alert per 5 h
reset; mkdir -p "$T/logs/triage"
export AGENT_BUDGET_5H_USD=3   # small limit for these cases; the default ($60) is checked below
recent="$(jq -rn "now - 3600 | floor | todate")"; old="$(jq -rn "now - 6 * 3600 | floor | todate")"
echo "{\"agent\":\"triage\",\"started\":\"$old\",\"result\":{\"total_cost_usd\":9.0}}" > "$T/logs/triage/x.jsonl"
echo "{\"agent\":\"triage\",\"started\":\"$recent\",\"result\":{\"total_cost_usd\":2.5}}" >> "$T/logs/triage/x.jsonl"
echo 'not json' >> "$T/logs/triage/x.jsonl"
run ok
check "under budget (old runs don't count): runs" '[ "$(calls)" = 1 ]'
mkdir -p "$T/logs/analyst"   # the analyst's (Opus) runs count towards the same budget
echo "{\"agent\":\"analyst\",\"started\":\"$recent\",\"result\":{\"total_cost_usd\":0.6}}" >> "$T/logs/analyst/x.jsonl"
run ok
check "over budget (analyst runs count): skipped, claude not called" '[ "$(calls)" = 1 ] && grep -q "budget: \\\$3.11 spent" "$L/skipped.log"'
check "over budget: one alert" '[ "$(alerts)" = 1 ] && grep -q "Budget reached" "$T/telegram"'
run ok
check "over budget again: no second alert" '[ "$(alerts)" = 1 ] && [ "$(calls)" = 1 ]'
AGENT_BUDGET_5H_USD=5 run ok
check "budget is configurable" '[ "$(calls)" = 2 ]'
AGENT_BUDGET_5H_USD=off run ok
check "budget off" '[ "$(calls)" = 3 ]'
unset AGENT_BUDGET_5H_USD
run ok
check "default budget is \$60 (3.14 spent: runs)" '[ "$(calls)" = 4 ]'
echo "{\"agent\":\"triage\",\"started\":\"$recent\",\"result\":{\"total_cost_usd\":57}}" >> "$T/logs/triage/x.jsonl"
run ok
check "default budget: over \$60 skips" '[ "$(calls)" = 4 ] && grep -q "limit \\\$60" "$L/skipped.log"'

# 8. alerts off / no token: logged only, never sent
reset; AGENT_ALERTS=off FAKE_MODE=fail bash -c 'for i in 1 2 3; do bash "$0" release; done' "$ROOT/infra/bin/run-agent.sh" >/dev/null 2>&1
check "AGENT_ALERTS=off: logged, not sent" '[ "$(alerts)" = 0 ] && grep -q "paused after 3 failed runs" "$T/logs/alerts.log"'


# 9. pull step: the repo clones are pulled from their code host with the agents' read-only tokens
# (routing.yaml code_hosts → CODEHOST_<KEY>_TOKEN; GitLab and GitHub), passed through GIT_CONFIG_*
# (never argv); release ticks don't pull them; a failed pull doesn't stop a tick
mkdir -p "$T/git-bin" "$T/repos/billing-backend" "$T/repos/warehouse" "$T/repos/broken"
cat >"$T/git-bin/git" <<'EOF2'
#!/usr/bin/env bash
# fake git: records argv and the auth config it was given; `remote get-url` reads <dir>/.origin
echo "$*" >> "$FAKE_DIR/git-argv"
[ "$1" = -C ] && d="$2" || d=.
echo "$d|${GIT_CONFIG_COUNT:-}|${GIT_CONFIG_KEY_0:-}|${GIT_CONFIG_VALUE_0:-}|${GIT_CONFIG_KEY_1:-}=${GIT_CONFIG_VALUE_1-unset}|${GIT_TERMINAL_PROMPT:-}" >> "$FAKE_DIR/git-env"
case "$*" in *"remote get-url origin") cat "$d/.origin";; *broken*pull*) exit 1;; esac
EOF2
chmod +x "$T/git-bin/git"
# the example routing.yaml: main = GitLab at gitlab.example.com, oss = GitHub at github.com
echo https://gitlab.example.com/acme/billing-backend.git > "$T/repos/billing-backend/.origin"
echo https://github.com/acme/warehouse.git > "$T/repos/warehouse/.origin"
echo https://gitlab.example.com/acme/broken.git > "$T/repos/broken/.origin"
pull_run() { rm -f "$T/git-argv" "$T/git-env"; PATH="$T/git-bin:$PATH" AGENT_GIT_PULL=on AGENT_REPOS_DIR="$T/repos" \
  AGENT_OPS_ENV=/dev/null CODEHOST_MAIN_TOKEN=main-tok-111 CODEHOST_OSS_TOKEN=oss-tok-222 run ok "$1"; }
b64() { printf '%s:%s' "$1" "$2" | base64 | tr -d '\n'; }
reset; pull_run triage
check "triage pulls every repo, ff-only" '[ "$(grep -c -- "pull -q --ff-only" "$T/git-argv")" = 4 ] && grep -q -- "-C $T/repos/warehouse pull -q --ff-only" "$T/git-argv"'
reset; pull_run analyst
check "analyst pulls every repo too" '[ "$(grep -c -- "pull -q --ff-only" "$T/git-argv")" = 4 ]'
reset; pull_run triage
check "GitLab repo: its host's read token (oauth2)" 'grep -q "^$T/repos/billing-backend|2|http.https://gitlab.example.com/.extraHeader|Authorization: Basic $(b64 oauth2 main-tok-111)|credential.helper=|0$" "$T/git-env"'
check "GitHub repo: its host's read token (x-access-token)" 'grep -q "^$T/repos/warehouse|2|http.https://github.com/.extraHeader|Authorization: Basic $(b64 x-access-token oss-tok-222)|credential.helper=|0$" "$T/git-env"'
check "tokens never in git argv" '! grep -qE "main-tok|oss-tok|$(b64 oauth2 main-tok-111)|$(b64 x-access-token oss-tok-222)" "$T/git-argv"'
check "failed pull: tick still runs" '[ "$(calls)" = 1 ] && grep -q -- "-C $T/repos/broken pull" "$T/git-argv"'
reset; pull_run release
check "release doesn't pull the repo clones" '! grep -q "$T/repos" "$T/git-argv" && [ "$(calls)" = 1 ]'

exit "$fail"
