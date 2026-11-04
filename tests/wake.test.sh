#!/usr/bin/env bash
# Offline test for infra/macos/wake.py, once per supervisor: launchd (fake `launchctl`) and systemd
# (fake `systemctl`) on PATH record calls; the agent counts as running while $T/running exists.
#   bash tests/wake.test.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"
pid=""
cleanup() { if [ -n "$pid" ]; then kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null || true; fi; rm -rf "$T"; }
trap cleanup EXIT
PY="$(command -v python3)"

mkdir "$T/bin"
cat > "$T/bin/launchctl" <<EOF2
#!/bin/bash
echo "\$*" >> "$T/calls"
if [ "\$1" = print ]; then
  if [ -e "$T/running" ]; then echo "	state = running"; else echo "	state = not running"; fi
fi
exit 0
EOF2
cat > "$T/bin/systemctl" <<EOF2
#!/bin/bash
echo "\$*" >> "$T/calls"
if [ "\$1" = is-active ]; then
  if [ -e "$T/running" ]; then echo activating; else echo inactive; exit 3; fi
fi
exit 0
EOF2
chmod +x "$T/bin/launchctl" "$T/bin/systemctl"
"$T/bin/launchctl" print warmup >/dev/null  # first exec of a new script can be slow on macOS
echo 'WAKE_TOKEN="s3cret-test"' > "$T/env"

fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   [$sup] $1"; else echo "FAIL [$sup] $1: got '$2', want '$3'"; fail=1; fi; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

suite() {  # suite <launchd|systemd> <kick-line-regex>
  sup="$1"; want="$2"
  : > "$T/calls"; rm -f "$T/running"
  port="$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
  PATH="$T/bin:$PATH" AGENT_OPS_ENV="$T/env" WAKE_BIND=127.0.0.1 WAKE_PORT="$port" WAKE_SUPERVISOR="$sup" \
    WAKE_DEBOUNCE=1 WAKE_POLL=0.5 "$PY" "$ROOT/infra/macos/wake.py" > "$T/out" 2>&1 &
  pid=$!
  for _ in $(seq 1 50); do curl -fs -o /dev/null "http://127.0.0.1:$port/health" && break; sleep 0.1; done
  wake() { code -X POST -H "X-Wake-Token: ${1:-s3cret-test}" -d '{"x":1}' "http://127.0.0.1:$port/wake/${2:-triage}"; }
  kicks() { grep -cE "$want" "$T/calls" || true; }
  wait_kicks() { local i; for i in $(seq 1 $(( $2 * 10 ))); do [ "$(kicks)" -ge "$1" ] && break; sleep 0.1; done; sleep 1; }

  check "health 200" "$(code "http://127.0.0.1:$port/health")" 200
  check "missing token 401" "$(code -X POST "http://127.0.0.1:$port/wake/triage")" 401
  check "wrong token 401" "$(wake wrong)" 401
  check "unknown agent 404" "$(wake s3cret-test release)" 404
  check "other path 404" "$(code -X POST -H 'X-Wake-Token: s3cret-test' "http://127.0.0.1:$port/foo")" 404
  check "nothing kicked yet" "$(kicks)" 0

  check "wake 202" "$(wake)" 202
  sleep 0.5; check "no kick before debounce" "$(kicks)" 0
  wait_kicks 1 5; check "one wake -> one kick" "$(kicks)" 1

  : > "$T/calls"
  for _ in 1 2 3 4 5; do wake >/dev/null; done
  wait_kicks 1 5; check "5 rapid wakes -> one kick" "$(kicks)" 1

  : > "$T/calls"; touch "$T/running"
  wake >/dev/null
  sleep 3; check "no kick while running" "$(kicks)" 0
  rm -f "$T/running"
  wait_kicks 1 5; check "kick after it stops" "$(kicks)" 1
  sleep 1; check "only one kick after it stops" "$(kicks)" 1

  grep -q 'x":1' "$T/out" && { echo "FAIL [$sup] request body in log"; fail=1; } || echo "ok   [$sup] no body in log"
  grep -q 's3cret' "$T/out" && { echo "FAIL [$sup] token in log"; fail=1; } || echo "ok   [$sup] no token in log"
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null || true; pid=""
}

suite launchd "^kickstart gui/$(id -u)/io\.agent-ops\.agent-triage$"
suite systemd "^start --no-block agent@triage\.service$"
[ "$fail" = 0 ] || { echo "--- wake.py output"; cat "$T/out"; exit 1; }
echo "all wake tests passed"
