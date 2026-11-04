#!/usr/bin/env bash
# Publishes a local service on a gateway with an SSH remote forward, for hosts that others can't
# reach directly (e.g. the agent host on an isolated network): gateway BIND:REMOTE_PORT → this
# host's LOCAL_HOST:LOCAL_PORT. Used for the task board's web UI and, separately, for BI access to
# its database. The gateway's sshd needs `GatewayPorts clientspecified` to bind 0.0.0.0.
# Runs in the foreground; launchd (publish-tunnel.plist.template) or systemd restarts it.
#
#   TUNNEL_TARGET=agent@192.0.2.20 PUBLISH_PORT=8090 bash infra/macos/publish-tunnel.sh
#
# Settings: TUNNEL_TARGET / TUNNEL_TARGET_PORT / TUNNEL_JUMP / TUNNEL_KEY as in tunnel.sh;
# PUBLISH_BIND (0.0.0.0), PUBLISH_PORT (8090), PUBLISH_LOCAL_HOST (localhost), PUBLISH_LOCAL_PORT (80).
set -euo pipefail
KEY="${TUNNEL_KEY:-$HOME/.ssh/id_ed25519}"
TARGET="${TUNNEL_TARGET:?set TUNNEL_TARGET=user@host}"
TARGET_PORT="${TUNNEL_TARGET_PORT:-22}"
REMOTE_BIND="${PUBLISH_BIND:-0.0.0.0}"
REMOTE_PORT="${PUBLISH_PORT:-8090}"
LOCAL_HOST="${PUBLISH_LOCAL_HOST:-localhost}"
LOCAL_PORT="${PUBLISH_LOCAL_PORT:-80}"

opts=(-o IdentitiesOnly=yes)
[ "$(uname)" = Darwin ] && opts+=(-o UseKeychain=yes)
[ -t 0 ] || opts+=(-o BatchMode=yes)
jump=()
if [ -n "${TUNNEL_JUMP:-}" ]; then
  jhost="${TUNNEL_JUMP%:*}"; jport=22
  [ "$jhost" != "$TUNNEL_JUMP" ] && jport="${TUNNEL_JUMP##*:}"
  jump=(-o "ProxyCommand=ssh -F /dev/null -i $KEY ${opts[*]} -p $jport -W %h:%p $jhost")
fi

# ExitOnForwardFailure: if the gateway still holds the port from a dropped session, exit and let
# the supervisor retry (every 5 s) until the old session times out and frees it.
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
echo "$(ts) start" >&2
set +e
ssh -F /dev/null -N -i "$KEY" "${opts[@]}" "${jump[@]}" -p "$TARGET_PORT" \
  -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -o ConnectTimeout=10 -o ExitOnForwardFailure=yes \
  -R "$REMOTE_BIND:$REMOTE_PORT:$LOCAL_HOST:$LOCAL_PORT" \
  "$TARGET" 2> >(while IFS= read -r line; do echo "$(ts) $line"; done >&2)
rc=$?
echo "$(ts) ssh exited $rc" >&2
exit "$rc"
