#!/usr/bin/env bash
# Installs or updates Plane Community Edition (the default task board) on the agent host: macOS
# (Docker Desktop) or Linux (Docker Engine + compose plugin). Idempotent: safe to run again. Pins the
# version in ./VERSION. Run as the user that owns Docker.
#
#   bash infra/plane/install.sh <host-name-or-ip> [http-port]
#   e.g. bash infra/plane/install.sh plane.example.com              # → http://plane.example.com
#   bash infra/plane/install.sh https://plane.example.com            # full URL (TLS at a proxy)
#   bash infra/plane/install.sh https://tools.example.com/plane      # under a path prefix
#
# The first argument is the address people type (WEB_URL). When that is not this machine (e.g. a
# gateway tunnel, infra/macos/publish-tunnel.sh, behind a reverse proxy), keep Plane listening on
# :80 here and keep the machine address the agents and the n8n webhook use:
#   PLANE_LISTEN_PORT=80 PLANE_LOCAL_HOST=192.0.2.10 bash infra/plane/install.sh https://tools.example.com/plane
# A full URL implies PLANE_LISTEN_PORT=80 unless set. A path in it (/plane) serves the UI under
# that prefix: build the images first with infra/plane/subpath/build.sh (stock images can't).
# Restart with this script, not setup.sh's menu: setup.sh only knows the stock compose file.
#
# Same layout as Plane's own setup.sh (~/plane-selfhost/plane-app), so its menu still works there for
# logs, backups and upgrades. Generated passwords live only in plane.env on this host, never in git.
set -euo pipefail

ADDR="${1:?usage: install.sh <host-name-or-ip> [http-port] | <http(s)://host[:port][/path]>}"
if [[ "$ADDR" =~ ^(https?)://([^/:]+)(:([0-9]+))?(/.*)?$ ]]; then
  # Full URL: the public address sits behind a proxy, so Plane keeps its local port.
  HOST="${BASH_REMATCH[2]}"
  origin="${BASH_REMATCH[1]}://$HOST${BASH_REMATCH[3]}"
  PREFIX="${BASH_REMATCH[5]%/}"
  [ -z "$PREFIX" ] || [[ "$PREFIX" =~ ^/[a-z0-9/-]+[a-z0-9]$ ]] || { echo "path must look like /plane" >&2; exit 1; }
  web_url="$origin$PREFIX"
  LISTEN_PORT="${PLANE_LISTEN_PORT:-80}"
else
  HOST="$ADDR"
  PORT="${2:-80}"
  origin="http://$HOST"; [ "$PORT" = 80 ] || origin="$origin:$PORT"
  web_url="$origin"
  PREFIX=""
  LISTEN_PORT="${PLANE_LISTEN_PORT:-$PORT}"
fi
LOCAL_HOST="${PLANE_LOCAL_HOST:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$HERE/VERSION")"
DIR="${PLANE_DIR:-$HOME/plane-selfhost/plane-app}"
URL="https://github.com/makeplane/plane/releases/download/$VERSION"

log() { echo "[plane] $*"; }

docker info >/dev/null 2>&1 || { echo "Docker is not running (macOS: start Docker Desktop; Linux: systemctl start docker)" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "the docker compose plugin is missing" >&2; exit 1; }

compose=(-f docker-compose.yaml)
if [ -n "$PREFIX" ]; then
  # UI under a path prefix: images from subpath/build.sh, layered by docker-compose.subpath.yaml.
  SUBPATH_TAG="$VERSION-$(echo "${PREFIX#/}" | tr '/' '-')"
  for app in web admin space backend; do
    docker image inspect "plane-$app:$SUBPATH_TAG" >/dev/null 2>&1 || {
      echo "missing image plane-$app:$SUBPATH_TAG: run PLANE_PREFIX=$PREFIX bash $HERE/subpath/build.sh first" >&2; exit 1; }
  done
  compose+=(-f docker-compose.subpath.yaml)
fi

mkdir -p "$DIR/archive"
cd "$DIR"
# Metabase access (infra/plane/bi): Postgres on 127.0.0.1:$PLANE_BI_PORT when plane.env sets it.
if grep -qE '^PLANE_BI_PORT=[0-9]+$' plane.env 2>/dev/null; then
  cp "$HERE/bi/docker-compose.bi.yaml" docker-compose.bi.yaml
  compose+=(-f docker-compose.bi.yaml)
fi

# 1. Pinned compose file + Plane's env template, plus setup.sh for its upgrade/backup menu.
log "downloading $VERSION"
curl -fsSL "$URL/docker-compose.yml" -o docker-compose.yaml
curl -fsSL "$URL/variables.env" -o variables.env
curl -fsSL "$URL/setup.sh" -o ../setup.sh && chmod +x ../setup.sh

# 2. plane.env: created once with generated secrets; later runs only update version and address.
set_env() {  # key value
  # Portable in-place edit (BSD and GNU sed differ on -i).
  if grep -q "^$1=" plane.env; then
    local tmp; tmp="$(mktemp)"; sed "s|^$1=.*|$1=$2|" plane.env > "$tmp" && cat "$tmp" > plane.env && rm -f "$tmp"
  else echo "$1=$2" >> plane.env; fi
}
rand() { openssl rand -hex "${1:-24}"; }

if [ ! -f plane.env ]; then
  log "creating plane.env with generated passwords"
  cp variables.env plane.env
  chmod 600 plane.env
  pg_pass="$(rand)"; mq_pass="$(rand)"
  set_env POSTGRES_PASSWORD "$pg_pass"
  set_env DATABASE_URL "postgresql://plane:$pg_pass@plane-db/plane"
  set_env RABBITMQ_PASSWORD "$mq_pass"
  set_env AMQP_URL "amqp://plane:$mq_pass@plane-mq:5672/plane"
  set_env SECRET_KEY "$(rand 32)"
  set_env LIVE_SERVER_SECRET_KEY "$(rand 32)"
  set_env AWS_ACCESS_KEY_ID "plane$(rand 6)"
  set_env AWS_SECRET_ACCESS_KEY "$(rand)"
else
  cp plane.env "archive/$(date +%Y%m%d-%H%M%S).env"
fi

set_env APP_RELEASE "$VERSION"
set_env APP_DOMAIN "$HOST"
set_env LISTEN_HTTP_PORT "$LISTEN_PORT"
set_env WEB_URL "$web_url"
origins="$origin"  # CORS wants the origin, without a path
if [ -n "$LOCAL_HOST" ]; then
  local_url="http://$LOCAL_HOST"; [ "$LISTEN_PORT" = 80 ] || local_url="$local_url:$LISTEN_PORT"
  origins="$origins,$local_url"
fi
set_env CORS_ALLOWED_ORIGINS "$origins"
set_env PLANE_PREFIX "$PREFIX"
if [ -n "$PREFIX" ]; then
  set_env PLANE_SUBPATH_TAG "$SUBPATH_TAG"
  cp "$HERE/subpath/Caddyfile" Caddyfile.subpath
  cp "$HERE/subpath/docker-compose.subpath.yaml" docker-compose.subpath.yaml
fi
# Plane refuses webhooks to private addresses and to its own hostname (loop-back guard). n8n runs
# on this same host, so trust the host itself: an IP must be in WEBHOOK_ALLOWED_IPS for the private
# address check, and any host in WEBHOOK_ALLOWED_HOSTS skips both checks.
# Override with PLANE_WEBHOOK_ALLOWED_IPS / PLANE_WEBHOOK_ALLOWED_HOSTS.
def_hosts=""; def_ips=""
for h in "$HOST" $LOCAL_HOST; do
  def_hosts="${def_hosts:+$def_hosts,}$h"
  [[ "$h" =~ ^[0-9.]+$ ]] && def_ips="${def_ips:+$def_ips,}$h"
done
set_env WEBHOOK_ALLOWED_IPS "${PLANE_WEBHOOK_ALLOWED_IPS:-$def_ips}"
set_env WEBHOOK_ALLOWED_HOSTS "${PLANE_WEBHOOK_ALLOWED_HOSTS:-$def_hosts}"

# 3. Start (or restart with the new version/config).
log "starting"
docker compose "${compose[@]}" --env-file plane.env up -d --pull missing --quiet-pull

log "waiting for $web_url (database migrations take about a minute on first start)"
for _ in $(seq 1 60); do
  if curl -fs -o /dev/null "http://localhost:$LISTEN_PORT/api/instances/"; then
    log "up: $web_url (first visit → $web_url/god-mode to create the instance admin)"
    exit 0
  fi
  sleep 5
done
echo "Plane did not answer within 5 minutes: check 'docker compose -f $DIR/docker-compose.yaml logs'" >&2
exit 1
