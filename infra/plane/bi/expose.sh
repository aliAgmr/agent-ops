#!/usr/bin/env bash
# Publishes Plane's Postgres on this host's loopback (127.0.0.1:$PORT, default 15432) for the
# read-only BI user, by layering docker-compose.bi.yaml over the running Plane stack and recreating
# only the database container (Plane is unavailable for a few seconds). Idempotent.
# install.sh keeps the layer on later runs (it reads PLANE_BI_PORT from plane.env).
#
#   bash infra/plane/bi/expose.sh [port]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${PLANE_DIR:-$HOME/plane-selfhost/plane-app}"
PORT="${1:-15432}"
cd "$DIR"
cp "$HERE/docker-compose.bi.yaml" docker-compose.bi.yaml
if grep -qE '^PLANE_BI_PORT=' plane.env; then
  tmp="$(mktemp)"; sed -E "s/^PLANE_BI_PORT=.*/PLANE_BI_PORT=$PORT/" plane.env > "$tmp" && cat "$tmp" > plane.env && rm -f "$tmp"
else
  echo "PLANE_BI_PORT=$PORT" >> plane.env
fi
compose=(-f docker-compose.yaml)
grep -qE '^PLANE_PREFIX=.+' plane.env && compose+=(-f docker-compose.subpath.yaml)
compose+=(-f docker-compose.bi.yaml)
docker compose "${compose[@]}" --env-file plane.env up -d plane-db
for _ in $(seq 1 30); do nc -z 127.0.0.1 "$PORT" 2>/dev/null && break; sleep 1; done
nc -z 127.0.0.1 "$PORT" && echo "[bi] Plane's Postgres on 127.0.0.1:$PORT" || { echo "[bi] port $PORT not answering" >&2; exit 1; }
for _ in $(seq 1 30); do curl -fs -o /dev/null http://localhost/api/instances/ && { echo "[bi] Plane API answering"; exit 0; }; sleep 2; done
echo "[bi] Plane API not answering yet: check 'docker compose logs api'" >&2; exit 1
