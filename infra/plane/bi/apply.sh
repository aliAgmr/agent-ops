#!/usr/bin/env bash
# Creates or updates the read-only BI user `metabase_ro` and the `bi` views in Plane's database
# (setup.sql). Idempotent: re-run after every Plane upgrade. Run it yourself on the agent host; the
# agents' guard doesn't let them open database clients.
#
#   bash infra/plane/bi/apply.sh            # create/update; prints the row count of each view
#   bash infra/plane/bi/apply.sh --rotate   # also generate a new password (update it in Metabase)
#
# The password is generated once and kept in ~/.config/agent-ops/plane-bi.password (mode 600). It is
# never printed: copy it into your BI tool from that file (macOS: `pbcopy < <file>`; Linux:
# `xclip -selection clipboard < <file>`). Task links in the views use plane.env's WEB_URL and the
# workspace slug (TRACKER_WORKSPACE, default from the agents' env file).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${PLANE_DIR:-$HOME/plane-selfhost/plane-app}"
CONTAINER="${PLANE_DB_CONTAINER:-plane-app-plane-db-1}"
PW_FILE="${PLANE_BI_PASSWORD_FILE:-$HOME/.config/agent-ops/plane-bi.password}"

envval() { grep -E "^$1=" "$DIR/plane.env" | tail -1 | cut -d= -f2-; }
DB_USER="$(envval POSTGRES_USER)"; DB_NAME="$(envval POSTGRES_DB)"
[ -n "$DB_USER" ] && [ -n "$DB_NAME" ] || { echo "POSTGRES_USER/POSTGRES_DB not found in $DIR/plane.env" >&2; exit 1; }
[ "$DB_NAME" = plane ] || { echo "setup.sql grants CONNECT on database 'plane'; this one is '$DB_NAME'" >&2; exit 1; }

mkdir -p "$(dirname "$PW_FILE")"
if [ ! -s "$PW_FILE" ] || [ "${1:-}" = --rotate ]; then
  # openssl, not `tr </dev/urandom | head`: under pipefail tr dies of SIGPIPE and kills the script.
  (umask 077; openssl rand -hex 16 > "$PW_FILE")
  echo "[bi] new password in $PW_FILE"
fi
chmod 600 "$PW_FILE"

# The password goes in through stdin (a \set line), never through a command-line argument.
# -h /var/run/postgresql: the container sets PGHOST=plane-db (TCP, which needs Plane's own password);
# the local socket is trusted by the image's pg_hba. -w: never stop to ask for a password.
WEB_URL="$(envval WEB_URL)"
WS="${TRACKER_WORKSPACE:-$(grep -E '^TRACKER_WORKSPACE=' "$HOME/.config/agent-ops/env" 2>/dev/null | tail -1 | cut -d= -f2-)}"
[ -n "$WS" ] || { echo "set TRACKER_WORKSPACE (the workspace slug) for the task links" >&2; exit 1; }
{ printf "\\set ro_password '%s'\n" "$(cat "$PW_FILE")"; sed -e "s|__PLANE_WEB_URL__|${WEB_URL%/}|g" -e "s|__WORKSPACE__|$WS|g" "$HERE/setup.sql"; } |
  docker exec -i "$CONTAINER" psql -X -q -w -h /var/run/postgresql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -P pager=off
echo "[bi] done: user metabase_ro, schema bi (read-only)"
