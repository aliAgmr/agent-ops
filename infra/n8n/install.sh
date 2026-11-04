#!/usr/bin/env bash
# Installs or updates n8n on the agent host (Docker). Idempotent: safe to run again.
#
#   bash infra/n8n/install.sh <host-name-or-ip> [port]
#   e.g. bash infra/n8n/install.sh n8n.example.com          # → http://n8n.example.com:5678
#
# The running copy lives in ~/n8n-selfhost (compose file + .env). The .env holds the generated
# encryption key for stored credentials: it stays on the host, and losing it makes the saved
# Telegram/Mattermost credentials unreadable, so back it up with the n8n volume.
set -euo pipefail
HOST="${1:?usage: install.sh <host-name-or-ip> [port]}"
PORT="${2:-5678}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${N8N_DIR:-$HOME/n8n-selfhost}"
VERSION="$(tr -d '[:space:]' < "$HERE/VERSION")"
log() { echo "[n8n] $*"; }

docker info >/dev/null 2>&1 || { echo "Docker is not running" >&2; exit 1; }
mkdir -p "$DIR" && cd "$DIR"
cp "$HERE/docker-compose.yml" docker-compose.yml

set_env() {  # key value
  if grep -q "^$1=" .env 2>/dev/null; then
    local tmp; tmp="$(mktemp)"; awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v} {print}' .env > "$tmp"
    cat "$tmp" > .env; rm -f "$tmp"
  else echo "$1=$2" >> .env; fi
}
if [ ! -f .env ]; then
  log "creating .env with a generated encryption key"
  umask 077; : > .env
  set_env N8N_ENCRYPTION_KEY "$(openssl rand -hex 32)"
fi
chmod 600 .env
set_env N8N_VERSION "$VERSION"
set_env N8N_HOST "$HOST"
set_env N8N_PORT "$PORT"

log "starting $VERSION"
docker compose -p n8n up -d --pull missing --quiet-pull
for _ in $(seq 1 30); do
  if curl -fs -o /dev/null "http://localhost:$PORT/healthz"; then
    log "up: http://$HOST:$PORT (first visit creates the owner account)"; exit 0
  fi
  sleep 2
done
echo "n8n did not answer within a minute: docker compose -p n8n logs" >&2; exit 1
