#!/usr/bin/env bash
# SSH tunnel to the agents' read-only data sources (ClickHouse, Postgres, Jenkins …) when they are
# only reachable through a gateway. Local forwards only. Runs in the foreground; launchd (macOS,
# tunnel.plist.template) or systemd (Linux, agent-tunnel.service) keeps it alive.
#
#   TUNNEL_TARGET=agent@192.0.2.20 \
#   TUNNEL_FORWARDS="18123:ch.example.com:8123 15431:db.example.com:5432" bash infra/macos/tunnel.sh
#
# Settings: TUNNEL_TARGET (user@host, required), TUNNEL_TARGET_PORT (22), TUNNEL_JUMP (optional
# user@host[:port] jump host), TUNNEL_KEY (~/.ssh/id_ed25519), TUNNEL_FORWARDS (required:
# space-separated local_port:remote_host:remote_port). First run it by hand in a terminal so you can
# confirm the host fingerprints.
set -euo pipefail
KEY="${TUNNEL_KEY:-$HOME/.ssh/id_ed25519}"
TARGET="${TUNNEL_TARGET:?set TUNNEL_TARGET=user@host}"
TARGET_PORT="${TUNNEL_TARGET_PORT:-22}"
FORWARDS="${TUNNEL_FORWARDS:?set TUNNEL_FORWARDS=\"local:host:port …\"}"

opts=(-o IdentitiesOnly=yes)
# macOS: the key's passphrase comes from the Keychain (ssh-add --apple-use-keychain <key>).
[ "$(uname)" = Darwin ] && opts+=(-o UseKeychain=yes)
# Under launchd/systemd there is no terminal: BatchMode makes a missing passphrase fail fast instead
# of hanging; the supervisor retries.
[ -t 0 ] || opts+=(-o BatchMode=yes)
jump=()
if [ -n "${TUNNEL_JUMP:-}" ]; then
  jhost="${TUNNEL_JUMP%:*}"; jport=22
  [ "$jhost" != "$TUNNEL_JUMP" ] && jport="${TUNNEL_JUMP##*:}"
  jump=(-o "ProxyCommand=ssh -F /dev/null -i $KEY ${opts[*]} -p $jport -W %h:%p $jhost")
fi
fwd=()
for f in $FORWARDS; do fwd+=(-L "$f"); done

# ssh's own messages carry no time: prefix every stderr line with UTC time, and log each start and
# exit, so drops can be matched against the gateway side.
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
echo "$(ts) start" >&2
set +e
ssh -F /dev/null -N -i "$KEY" "${opts[@]}" "${jump[@]}" -p "$TARGET_PORT" \
  -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -o ConnectTimeout=10 -o ExitOnForwardFailure=yes \
  "${fwd[@]}" "$TARGET" 2> >(while IFS= read -r line; do echo "$(ts) $line"; done >&2)
rc=$?
echo "$(ts) ssh exited $rc" >&2
exit "$rc"
