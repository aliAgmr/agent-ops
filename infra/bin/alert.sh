#!/usr/bin/env bash
# Sends one operator alert about the service agents as a private Telegram message to the question
# owner (routing.yaml `question_owner` → people.yaml `telegram_chat_id`), and always
# appends it to alerts.log. Called by run-agent.sh when an agent pauses, recovers or hits its budget.
#
#   alert.sh "<text>"
#
# Settings: TELEGRAM_BOT_TOKEN (the n8n bot), ALERT_CHAT_ID (overrides the chat id), AGENT_ALERTS=off
# (log only), AGENT_LOG_ROOT (where alerts.log goes). Never fails the caller: exit 0 even when
# Telegram is unreachable (the alert is still in alerts.log).
set -uo pipefail
text="${1:?usage: alert.sh <text>}"
root="$(cd "$(dirname "$0")/../.." && pwd)"
if [ "$(uname)" = Darwin ]; then default_logs="$HOME/Library/Logs/agent-ops"; else default_logs=/var/log/agents; fi
log_root="${AGENT_LOG_ROOT:-$default_logs}"
mkdir -p "$log_root"

# Strip anything that looks like a token or key before it leaves the machine.
text="$(printf '%s' "$text" | sed -E 's/[A-Za-z0-9_+\/=-]{32,}/[redacted]/g' | cut -c1-900)"
text="[agent-ops] $text"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '%s %s\n' "$ts" "$(printf '%s' "$text" | tr '\n' ' ')" >> "$log_root/alerts.log"

[ "${AGENT_ALERTS:-on}" = off ] && exit 0
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] || { echo "alert: TELEGRAM_BOT_TOKEN not set, logged only" >&2; exit 0; }

chat="${ALERT_CHAT_ID:-}"
if [ -z "$chat" ]; then
  chat="$(python3 -I - "$root/infra" <<'PY' 2>/dev/null
import sys; sys.path.insert(0, sys.argv[1]); import adapters as A
o = str(A.load_routing().get("question_owner") or "")
print(str(((A.load_people().get("people") or {}).get(o) or {}).get("telegram_chat_id") or ""))
PY
)"
fi
[ -n "$chat" ] || { echo "alert: no telegram_chat_id for the question owner, logged only" >&2; exit 0; }

# The token goes in through a curl config on stdin, so it never shows in `ps` or in curl's errors.
if ! printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TELEGRAM_BOT_TOKEN" \
    | curl -fsS -m 20 -K - --data-urlencode "chat_id=$chat" --data-urlencode "text=$text" \
      -o /dev/null 2>/dev/null; then
  echo "alert: Telegram send failed, logged only" >&2
else
  python3 -I "$root/infra/journal/journal.py" record --agent "${AGENT_NAME:-alert}" --system telegram \
    --action alert --target-type message --target-id question_owner --reversible no \
    --summary "Telegram alert to the question owner: ${text:0:120}" \
    --hint "A sent Telegram message can't be unsent: send a correction." >/dev/null 2>&1 || true
fi
exit 0
