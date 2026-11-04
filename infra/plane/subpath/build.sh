#!/usr/bin/env bash
# Builds Plane images that serve the UI under a path prefix (default /plane), e.g. for
# https://tools.example.com/plane behind a reverse proxy. Stock images compile their base paths in at
# build time and the web app has no base-path support, so this builds web/admin/space from the
# pinned source (../VERSION) with a small patch, and derives the backend image with a storage
# patch so upload URLs carry the prefix. Tags: plane-<app>:<version>-<prefix>, local only.
#
#   bash infra/plane/subpath/build.sh            # prefix /plane
#   PLANE_PREFIX=/pm bash infra/plane/subpath/build.sh
#
# Then apply with install.sh and a URL with that path; see infra/plane/README.md.
# Takes about 15–30 minutes the first time (pnpm install + three Vite builds); needs ~8 GB for Docker.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$HERE/../VERSION")"
PREFIX="${PLANE_PREFIX:-/plane}"
TAG="$VERSION-$(echo "${PREFIX#/}" | tr '/' '-')"
SRC="${PLANE_SRC_DIR:-$HOME/plane-selfhost/src}/plane-$VERSION"
log() { echo "[plane-subpath] $*"; }

[[ "$PREFIX" =~ ^/[a-z0-9/-]+[a-z0-9]$ ]] || { echo "PLANE_PREFIX must look like /plane" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running" >&2; exit 1; }

# 1. Pinned source, patched once (the clone is kept so later builds reuse it).
if [ ! -d "$SRC/.git" ]; then
  log "cloning plane $VERSION into $SRC"
  mkdir -p "$(dirname "$SRC")"
  git -c core.hooksPath=/dev/null clone -q --depth 1 --branch "$VERSION" https://github.com/makeplane/plane.git "$SRC"
fi
git -C "$SRC" checkout -q -- .   # start clean, so the patch applies the same every time
python3 "$HERE/patch_web.py" "$SRC"

# 2. Frontends. VITE_API_BASE_URL is the prefix itself, so the browser calls <prefix>/api/… and
# <prefix>/auth/…; the proxy (Caddyfile) strips the prefix again before Plane's services.
args=(
  --build-arg VITE_API_BASE_URL="$PREFIX"
  --build-arg VITE_WEB_BASE_PATH="$PREFIX"
  --build-arg VITE_ADMIN_BASE_PATH="$PREFIX/god-mode"
  --build-arg VITE_SPACE_BASE_PATH="$PREFIX/spaces"
  --build-arg VITE_LIVE_BASE_PATH="$PREFIX/live"
)
for app in web admin space; do
  log "building plane-$app:$TAG"
  docker build -q -f "$SRC/apps/$app/Dockerfile.$app" "${args[@]}" -t "plane-$app:$TAG" "$SRC"
done

# 3. Backend: stock image + storage patch (presigned upload/download URLs get X-Forwarded-Prefix).
log "building plane-backend:$TAG"
docker build -q --build-arg VERSION="$VERSION" -f "$HERE/backend.Dockerfile" -t "plane-backend:$TAG" "$HERE"

log "done: plane-{web,admin,space,backend}:$TAG"
