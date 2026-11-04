#!/usr/bin/env bash
# Saves the token on the clipboard into the agents' env file as KEY=<token>, replacing an existing
# KEY line, then clears the clipboard. The token is never printed or put on a command line.
#
#   bash infra/macos/set-env-from-clipboard.sh KEY       # e.g. CODEHOST_MAIN_TOKEN, TRACKER_TRIAGE_TOKEN
#
# Env file: AGENT_OPS_ENV, else ~/.config/agent-ops/env. Clipboard: pbpaste/pbcopy (macOS),
# wl-paste/wl-copy or xclip (Linux).
# Task-board tokens (TRACKER_*_TOKEN) and code-host tokens (CODEHOST_<KEY>_*TOKEN) are checked
# first, through infra/adapters, and the script shows whose token it is; a token that is rejected
# is not saved, so a wrong copy doesn't overwrite a working token.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

KEY="${1:?usage: set-env-from-clipboard.sh KEY (e.g. CODEHOST_MAIN_TOKEN)}"
ENV_FILE="${AGENT_OPS_ENV:-$HOME/.config/agent-ops/env}"
[[ "$KEY" =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "bad key name: $KEY" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "env file not found: $ENV_FILE" >&2; exit 1; }

clip_get() {
  if command -v pbpaste >/dev/null; then pbpaste
  elif command -v wl-paste >/dev/null; then wl-paste -n
  elif command -v xclip >/dev/null; then xclip -o -selection clipboard
  else echo "no clipboard tool (pbpaste, wl-paste or xclip)" >&2; return 1; fi
}
clip_clear() {
  if command -v pbcopy >/dev/null; then printf '' | pbcopy
  elif command -v wl-copy >/dev/null; then wl-copy --clear
  elif command -v xclip >/dev/null; then printf '' | xclip -selection clipboard; fi
}

token="$(clip_get | tr -d '\r\n')"
[ -n "$token" ] || { echo "clipboard is empty: copy the token first" >&2; exit 1; }
[[ "$token" =~ ^[A-Za-z0-9._~+/=:-]{16,}$ ]] \
  || { echo "clipboard doesn't look like a token (${#token} chars, spaces or odd characters); nothing saved" >&2; exit 1; }

# Check task-board and code-host tokens before saving. The token goes to Python in the environment.
check=""
case "$KEY" in
  TRACKER_*_TOKEN) check="tracker" ;;
  CODEHOST_*TOKEN) check="codehost" ;;
esac
if [ -n "$check" ]; then
  who="$(CANDIDATE_TOKEN="$token" AGENT_OPS_ENV="$ENV_FILE" python3 -I - "$ROOT/infra" "$check" "$KEY" <<'PY'
import os, re, sys
sys.path.insert(0, sys.argv[1])
import adapters as A
kind, key, tok = sys.argv[2], sys.argv[3], os.environ.pop("CANDIDATE_TOKEN")
try:
    if kind == "tracker":
        me = A.tracker(token=tok).whoami()
        print(me.get("email") or me.get("display_name") or me.get("id"))
    else:
        m = re.match(r"^CODEHOST_(.+?)_(?:RELEASE_|REVERT_)?TOKEN$", key)
        hosts = [h for h in (A.load_routing().get("code_hosts") or {}) if m and A.codehost_key(h) == m.group(1)]
        if not hosts:
            raise ValueError(f"no routing.yaml code host matches {key}")
        print(A.codehost(key=hosts[0], token=tok).whoami().get("username"))
except Exception as e:  # message has the token redacted (AdapterError)
    print(f"check failed: {e}", file=sys.stderr)
    sys.exit(1)
PY
)" || { echo "the $check rejected the token (or is unreachable / not configured yet); nothing saved" >&2; exit 1; }
  echo "token belongs to: $who"
fi

# Rewrite the file without the old KEY line, append the new one, keep it private.
tmp="$(mktemp "$ENV_FILE.XXXXXX")"
chmod 600 "$tmp"
grep -v "^$KEY=" "$ENV_FILE" > "$tmp" || true
printf '%s=%s\n' "$KEY" "$token" >> "$tmp"
mv "$tmp" "$ENV_FILE"
clip_clear
echo "saved $KEY (${#token} chars) to $ENV_FILE; clipboard cleared"
