#!/usr/bin/env bash
# Nightly backup of everything that lives only on the agent host (git has the rest). macOS and Linux.
#   task board (Plane): Postgres dump, uploaded files, plane.env + compose file
#   n8n:   data volume, workflows + credentials export (credentials stay encrypted), .env (the key
#          that decrypts them: back it up together with the data or the credentials are lost)
#   agents: the config folder (env file and tokens)
# A part that isn't installed here is skipped. Each run writes $BACKUP_DIR/<date>/ (owner-only),
# checks the dump, keeps $KEEP_DAYS days, and copies the folder to $OFFSITE_DIR when set (e.g. a
# mounted share). Set it: a backup on the same disk doesn't survive losing the host.
#
#   bash infra/macos/backup.sh     # launchd (install.sh BACKUP=true) or systemd (agent-backup.timer) runs it
#
# Settings (all optional):
#   BACKUP_DIR (~/agent-backups)  KEEP_DAYS (14)  OFFSITE_DIR
#   PLANE_DIR (~/plane-selfhost/plane-app)  PLANE_PROJECT (plane-app: its compose project name)
#   N8N_DIR (~/n8n-selfhost)  N8N_PROJECT (n8n)
#   CONFIG_DIR (~/.config/agent-ops; /etc/agent-ops on a Linux system install)
#   BACKUP_TOOL_IMAGE (postgres:15.7-alpine: any local image with sh + tar; Plane's own DB image)
set -euo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
BACKUP_DIR="${BACKUP_DIR:-$HOME/agent-backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
PLANE_DIR="${PLANE_DIR:-$HOME/plane-selfhost/plane-app}"
PLANE_PROJECT="${PLANE_PROJECT:-plane-app}"
N8N_DIR="${N8N_DIR:-$HOME/n8n-selfhost}"
N8N_PROJECT="${N8N_PROJECT:-n8n}"
CONFIG_DIR="${CONFIG_DIR:-$HOME/.config/agent-ops}"
TOOL_IMAGE="${BACKUP_TOOL_IMAGE:-postgres:15.7-alpine}"
DEST="$BACKUP_DIR/$(date +%Y-%m-%d)"
log() { echo "[backup] $(date -u +%H:%M:%SZ) $*"; }
running() { [ -n "$(docker ps -q --filter "name=^$1\$" 2>/dev/null)" ]; }

docker info >/dev/null 2>&1 || { echo "Docker is not running" >&2; exit 1; }
umask 077
mkdir -p "$DEST"; chmod 700 "$BACKUP_DIR" "$DEST"

vol_tar() {  # vol_tar <volume> <file>
  docker run --rm --entrypoint sh -v "$1:/v:ro" -v "$DEST:/b" "$TOOL_IMAGE" -c "tar czf /b/$2 -C /v ."
}

# Task board (Plane)
db="$PLANE_PROJECT-plane-db-1"
if running "$db"; then
  log "plane: database"
  docker exec "$db" sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h localhost -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$DEST/plane-db.dump"
  docker exec -i "$db" pg_restore --list < "$DEST/plane-db.dump" > /dev/null \
    || { echo "plane-db.dump failed its pg_restore --list check" >&2; exit 1; }
  log "plane: uploads"
  vol_tar "${PLANE_PROJECT}_uploads" plane-uploads.tgz
  for f in plane.env docker-compose.yaml; do [ -f "$PLANE_DIR/$f" ] && cp "$PLANE_DIR/$f" "$DEST/"; done
else
  log "plane: $db not running, skipped"
fi

# n8n
app="$N8N_PROJECT-n8n-1"
if running "$app"; then
  log "n8n: data, workflows, credentials"
  vol_tar "${N8N_PROJECT}_n8n_data" n8n-data.tgz
  for what in workflow credentials; do
    docker exec "$app" n8n "export:$what" --all --output="/tmp/n8n-$what.json" >/dev/null 2>&1
    docker exec -u root "$app" sh -c "cat /tmp/n8n-$what.json; rm -f /tmp/n8n-$what.json" > "$DEST/n8n-$what.json"
  done
  if [ -f "$N8N_DIR/.env" ]; then cp "$N8N_DIR/.env" "$DEST/n8n.env"; else log "n8n: no $N8N_DIR/.env (set N8N_DIR)"; fi
else
  log "n8n: $app not running, skipped"
fi

# agents
if [ -d "$CONFIG_DIR" ]; then
  log "agents: config and tokens"
  tar czf "$DEST/agent-ops-config.tgz" -C "$(dirname "$CONFIG_DIR")" "$(basename "$CONFIG_DIR")"
fi

chmod -R go-rwx "$DEST"
du -sh "$DEST" | awk '{print "[backup] size " $1}'

# retention: dated folders older than KEEP_DAYS
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20??-??-??' -mtime +"$KEEP_DAYS" -print -exec rm -rf {} + \
  | sed 's/^/[backup] removed old /'

if [ -n "${OFFSITE_DIR:-}" ]; then
  log "copying to $OFFSITE_DIR"
  rsync -a --delete "$BACKUP_DIR/" "$OFFSITE_DIR/"
else
  log "OFFSITE_DIR not set: this backup is only on this host"
fi
log "done: $DEST"
