#!/usr/bin/env bash
# Agent host setup on Linux (the counterpart of infra/macos/install.sh): the service agents run as
# systemd units of a dedicated `agents` user. Turns a fresh Debian/Ubuntu or Fedora/RHEL VM into the
# agent host, or brings an existing one up to date. Idempotent: re-run it after changing any
# setting below or after pulling a new version. Run as root. SETUP.md runs it.
#
#   sudo bash infra/bootstrap.sh                       # packages, user, env file, clones, units (timers off)
#   sudo ENABLE_TIMERS=true bash infra/bootstrap.sh    # also start the agents' schedules
#
# Settings (environment variables; all optional; the same names as infra/macos/install.sh):
#   AGENTS="triage:300 analyst:600 release:900"   which interval agents to run, every N seconds
#   AGENT_OPS_SRC                                 where /srv/agent-ops is cloned from (default: this checkout)
#   TRACKER_BASE_URL, TRACKER_WORKSPACE           written into the env file when given
#   CLAUDE_CODE_VERSION=stable                    pin an exact version once you have tested one
#   NIGHTLY=true NIGHTLY_ONCALENDAR="Mon..Fri 02:30" NIGHTLY_SEND_ONCALENDAR="Mon..Fri 08:30"
#                                                 the nightly pass and its messages (systemd OnCalendar
#                                                 syntax, the host's local time)
#   WAKE=true                                     instant triage from task-board webhooks (agent-wake, :8766)
#   AGENT_MCP=true AGENT_MCP_HOST=127.0.0.1       the project Q&A + action journal server (:8765)
#   JENKINS_WATCH=true JENKINS_WATCH_INTERVAL=1800 the Jenkins watcher agent (follows ENABLE_TIMERS) and its
#                                                 Telegram poller (telegram-poll.timer, started at once;
#                                                 infra/jenkins-watch/README.md)
#   BACKUP=true BACKUP_ONCALENDAR="*-*-* 03:15" OFFSITE_DIR=…  nightly backup (infra/macos/backup.sh)
#   TUNNEL=true TUNNEL_TARGET=… TUNNEL_FORWARDS=… SSH local forwards to data sources (tunnel.sh)
#   PUBLISH_TUNNEL=true TUNNEL_TARGET=… PUBLISH_PORT=8090 PUBLISH_LOCAL_PORT=80
#   BI_TUNNEL=true BI_REMOTE_PORT=15435 BI_LOCAL_PORT=15432 BI_BIND=0.0.0.0
#   TUNNEL_KEY=/home/agents/.ssh/id_ed25519       the tunnels' SSH key (owned by `agents`, no
#                                                 passphrase: systemd has no keychain to unlock it)
#
# The task board (infra/plane/install.sh) and n8n (infra/n8n/install.sh) are installed separately.
set -euo pipefail
[ "$(uname)" = Linux ] || { echo "Linux only; on macOS use infra/macos/install.sh" >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run as root (sudo bash $0)" >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC_ROOT="$(cd "$HERE/.." && pwd)"
ROOT=/srv/agent-ops
REPOS_DIR=/srv/repos
AGENT_USER=agents
CONF=/etc/agent-ops
ENV_FILE="$CONF/env"
UNITS=/etc/systemd/system
SYSTEMD="$HERE/systemd"
CLAUDE_CODE_VERSION="${CLAUDE_CODE_VERSION:-stable}"
AGENTS="${AGENTS:-triage:300 analyst:600 release:900}"
if [ "${JENKINS_WATCH:-false}" = "true" ]; then AGENTS="$AGENTS jenkins-watch:${JENKINS_WATCH_INTERVAL:-1800}"; fi
log() { echo "[bootstrap] $*"; }
as_agents() { runuser -u "$AGENT_USER" -- "$@"; }

# 1. Packages: git, jq, curl, Python 3, Node (npx runs some MCP servers), Docker for the task board and n8n.
log "packages"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git curl jq ca-certificates python3 nodejs npm >/dev/null
  command -v docker >/dev/null || apt-get install -y -qq docker.io >/dev/null
  docker compose version >/dev/null 2>&1 || apt-get install -y -qq docker-compose-v2 >/dev/null 2>&1 \
    || apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 || log "install the Docker Compose plugin yourself"
elif command -v dnf >/dev/null; then
  dnf install -y -q git curl jq ca-certificates python3 nodejs npm >/dev/null
  command -v docker >/dev/null || dnf install -y -q moby-engine docker-compose >/dev/null 2>&1 \
    || log "install Docker Engine + Compose yourself (https://docs.docker.com/engine/install/)"
else
  echo "no apt-get or dnf: install git curl jq python3 nodejs npm docker yourself, then re-run" >&2; exit 1
fi
if command -v docker >/dev/null; then systemctl enable --now docker >/dev/null 2>&1 || true; fi

# 2. uv / uvx (agent-mcp and several MCP servers run through them)
if ! command -v uv >/dev/null; then
  log "uv"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
fi
UV="$(command -v uv)"

# 3. Service user, folders, env file
if ! id "$AGENT_USER" >/dev/null 2>&1; then
  log "user $AGENT_USER"
  useradd --create-home --shell /bin/bash "$AGENT_USER"
fi
if getent group docker >/dev/null; then usermod -aG docker "$AGENT_USER"; fi
install -d -o "$AGENT_USER" -g "$AGENT_USER" /var/log/agents "$REPOS_DIR" /srv/nightly
install -d -m 750 -o root -g "$AGENT_USER" "$CONF"
if [ ! -f "$ENV_FILE" ]; then
  log "env file $ENV_FILE (from infra/env.template)"
  install -m 640 -o root -g "$AGENT_USER" "$SRC_ROOT/infra/env.template" "$ENV_FILE"
fi
chown root:"$AGENT_USER" "$ENV_FILE"; chmod 640 "$ENV_FILE"
set_env() {  # set_env NAME value: replace or append NAME=value (values are never printed)
  local tmp; tmp="$(mktemp)"
  grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
  printf '%s=%s\n' "$1" "$2" >> "$tmp"
  cat "$tmp" > "$ENV_FILE"; rm -f "$tmp"
}
if [ -n "${TRACKER_BASE_URL:-}" ]; then set_env TRACKER_BASE_URL "$TRACKER_BASE_URL"; fi
if [ -n "${TRACKER_WORKSPACE:-}" ]; then set_env TRACKER_WORKSPACE "$TRACKER_WORKSPACE"; fi
missing="$(grep -E '^(CLAUDE_CODE_OAUTH_TOKEN|TRACKER_BASE_URL|TRACKER_WORKSPACE|TRACKER_TRIAGE_TOKEN|CODEHOST_[A-Z0-9_]+_TOKEN)=$' "$ENV_FILE" | cut -d= -f1 | tr '\n' ' ' || true)"
if [ -n "$missing" ]; then log "still empty in $ENV_FILE (fill them with: sudoedit $ENV_FILE): $missing"; fi

# 4. Claude Code for the service user
if ! as_agents bash -lc 'command -v claude' >/dev/null 2>&1; then
  log "claude code ($CLAUDE_CODE_VERSION)"
  as_agents bash -c "curl -fsSL https://claude.ai/install.sh | bash -s $CLAUDE_CODE_VERSION" >/dev/null
fi

# 5. Deploy checkout + read-only clones of every repo in routing.yaml (infra/bin/repo-sync.sh, with
# the agents' read-only code host tokens from the env file). On a first install, fill the tokens
# and run this again.
if [ ! -d "$ROOT/.git" ]; then
  log "clone agent-ops → $ROOT"
  git clone -q "${AGENT_OPS_SRC:-$SRC_ROOT}" "$ROOT"
fi
chown -R "$AGENT_USER:$AGENT_USER" "$ROOT"
repo_sync() { as_agents bash -c 'set -a; . "$0"; set +a; exec bash /srv/agent-ops/infra/bin/repo-sync.sh "$@"' "$ENV_FILE" "$@"; }
# routing.yaml → "<clone url> <dir name>" per repo (code_hosts[host].url + path), each repo once.
repos="$(as_agents "$UV" run -q --no-project --with pyyaml==6.0.2 python - "$ROOT/routing.yaml" <<'PY'
import sys, yaml
r = yaml.safe_load(open(sys.argv[1])) or {}
hosts = r.get("code_hosts") or {}
seen = set()
def team_repos(teams):
    for t in (teams or {}).values():
        for repo in (t or {}).get("repos") or []:
            yield repo
for repo in list(team_repos(r.get("teams"))) + [x for p in (r.get("projects") or {}).values() for x in team_repos((p or {}).get("teams"))]:
    h = hosts.get(repo.get("host")) or {}
    if not h.get("url") or not repo.get("path") or repo["path"] in seen:
        continue
    seen.add(repo["path"])
    print(f"{h['url'].rstrip('/')}/{repo['path']}.git {repo['path'].rsplit('/', 1)[-1]}")
PY
)" || { log "could not read routing.yaml (uv + pyyaml); skipping repo clones"; repos=""; }
while read -r url name; do
  [ -n "${url:-}" ] || continue
  if [ -d "$REPOS_DIR/$name/.git" ]; then
    repo_sync pull "$REPOS_DIR/$name" >/dev/null 2>&1 || log "pull $name failed (token in $ENV_FILE?)"
  else
    log "clone $name"
    repo_sync clone "$url" "$REPOS_DIR/$name" || log "clone $name failed: code host token in $ENV_FILE? (re-run this script)"
  fi
done <<< "$repos"

# Service agents' MCP configs (service/*/.mcp.json, git-ignored) from their templates + routing.yaml;
# run-agent.sh re-renders before every tick, this covers agent-mcp and a first manual run.
as_agents env AGENT_OPS_ENV="$ENV_FILE" python3 -I "$ROOT/infra/bin/render-mcp.py" all >/dev/null \
  || log "render-mcp.py all failed (routing.yaml / env file?); re-run this script after fixing"

# 6. systemd units. Templates (*.template and agent-mcp.service) are rendered with sed; __X__ are
# placeholders.
log "systemd units"
render() {  # render <template> <unit name> [sed args…]
  local src="$1" dst="$UNITS/$2"; shift 2
  sed "$@" "$src" > "$dst.tmp" && chmod 644 "$dst.tmp" && mv "$dst.tmp" "$dst"
}
install -m 644 "$SYSTEMD/agent@.service" "$UNITS/"
timers=()
for spec in $AGENTS; do
  name="${spec%%:*}"; every="${spec#*:}"
  case "$name" in *[!a-z0-9-]*|"") echo "bad agent name in AGENTS: $spec" >&2; exit 1;; esac
  case "$every" in *[!0-9]*|"") echo "bad interval in AGENTS: $spec" >&2; exit 1;; esac
  render "$SYSTEMD/agent.timer.template" "agent-$name.timer" -e "s|__AGENT__|$name|g" -e "s|__INTERVAL__|$every|g"
  timers+=("agent-$name.timer")
done
enable_now=()   # long-running services and the backup timer start now, whatever ENABLE_TIMERS says
if [ "${NIGHTLY:-false}" = "true" ]; then
  install -m 644 "$SYSTEMD/nightly.service" "$SYSTEMD/nightly-send.service" "$UNITS/"
  render "$SYSTEMD/nightly.timer.template" nightly.timer -e "s|__ONCALENDAR__|${NIGHTLY_ONCALENDAR:-Mon..Fri 02:30}|"
  render "$SYSTEMD/nightly-send.timer.template" nightly-send.timer -e "s|__ONCALENDAR__|${NIGHTLY_SEND_ONCALENDAR:-Mon..Fri 08:30}|"
  timers+=(nightly.timer nightly-send.timer)
fi
if [ "${WAKE:-false}" = "true" ]; then
  install -m 644 "$SYSTEMD/agent-wake.service" "$UNITS/"; enable_now+=(agent-wake.service)
fi
if [ "${JENKINS_WATCH:-false}" = "true" ]; then
  # The poller starts at once, so `python3 infra/telegram/poll.py chats` can find the group's ids
  # before the agent's timer is on.
  install -m 644 "$SYSTEMD/telegram-poll.service" "$SYSTEMD/telegram-poll.timer" "$UNITS/"
  enable_now+=(telegram-poll.timer)
fi
if [ "${AGENT_MCP:-false}" = "true" ]; then
  render "$SYSTEMD/agent-mcp.service" agent-mcp.service -e "s|__AGENT_MCP_HOST__|${AGENT_MCP_HOST:-127.0.0.1}|g" -e "s|__UV__|$UV|g"
  enable_now+=(agent-mcp.service)
fi
write_conf() {  # write_conf <file> NAME=value…: a root-owned env file the agents user can read
  local f="$CONF/$1"; shift
  printf '%s\n' "$@" > "$f"; chown root:"$AGENT_USER" "$f"; chmod 640 "$f"
}
if [ "${BACKUP:-false}" = "true" ]; then
  install -m 644 "$SYSTEMD/agent-backup.service" "$UNITS/"
  render "$SYSTEMD/agent-backup.timer.template" agent-backup.timer -e "s|__ONCALENDAR__|${BACKUP_ONCALENDAR:-*-*-* 03:15}|"
  # PLANE_DIR / N8N_DIR: where infra/plane/install.sh and infra/n8n/install.sh put the running
  # copies (default: under the home of the user who ran them; backup.sh's defaults assume `agents`).
  backup_conf=("BACKUP_DIR=${BACKUP_DIR:-/srv/backups}" "OFFSITE_DIR=${OFFSITE_DIR:-}" "CONFIG_DIR=$CONF")
  for v in PLANE_DIR N8N_DIR PLANE_PROJECT N8N_PROJECT KEEP_DAYS; do
    if [ -n "${!v:-}" ]; then backup_conf+=("$v=${!v}"); fi
  done
  write_conf backup.env "${backup_conf[@]}"
  install -d -o "$AGENT_USER" -g "$AGENT_USER" "${BACKUP_DIR:-/srv/backups}"
  enable_now+=(agent-backup.timer)
fi
key="${TUNNEL_KEY:-/home/$AGENT_USER/.ssh/id_ed25519}"
tunnel_common=("TUNNEL_TARGET=${TUNNEL_TARGET:-}" "TUNNEL_TARGET_PORT=${TUNNEL_TARGET_PORT:-22}"
               "TUNNEL_JUMP=${TUNNEL_JUMP:-}" "TUNNEL_KEY=$key")
if [ "${TUNNEL:-false}" = "true" ]; then
  [ -n "${TUNNEL_TARGET:-}" ] && [ -n "${TUNNEL_FORWARDS:-}" ] || { echo "TUNNEL=true needs TUNNEL_TARGET and TUNNEL_FORWARDS" >&2; exit 1; }
  write_conf tunnel.env "${tunnel_common[@]}" "TUNNEL_FORWARDS=$TUNNEL_FORWARDS"
  install -m 644 "$SYSTEMD/agent-tunnel.service" "$UNITS/"; enable_now+=(agent-tunnel.service)
fi
if [ "${PUBLISH_TUNNEL:-false}" = "true" ]; then
  [ -n "${TUNNEL_TARGET:-}" ] || { echo "PUBLISH_TUNNEL=true needs TUNNEL_TARGET" >&2; exit 1; }
  write_conf publish-tunnel.env "${tunnel_common[@]}" "PUBLISH_BIND=${PUBLISH_BIND:-0.0.0.0}" \
    "PUBLISH_PORT=${PUBLISH_PORT:-8090}" "PUBLISH_LOCAL_HOST=localhost" "PUBLISH_LOCAL_PORT=${PUBLISH_LOCAL_PORT:-80}"
  install -m 644 "$SYSTEMD/agent-publish-tunnel.service" "$UNITS/"; enable_now+=(agent-publish-tunnel.service)
fi
if [ "${BI_TUNNEL:-false}" = "true" ]; then
  [ -n "${TUNNEL_TARGET:-}" ] || { echo "BI_TUNNEL=true needs TUNNEL_TARGET" >&2; exit 1; }
  write_conf bi-tunnel.env "${tunnel_common[@]}" "PUBLISH_BIND=${BI_BIND:-0.0.0.0}" \
    "PUBLISH_PORT=${BI_REMOTE_PORT:-15435}" "PUBLISH_LOCAL_HOST=127.0.0.1" "PUBLISH_LOCAL_PORT=${BI_LOCAL_PORT:-15432}"
  install -m 644 "$SYSTEMD/agent-bi-tunnel.service" "$UNITS/"; enable_now+=(agent-bi-tunnel.service)
fi
if [ "${TUNNEL:-false}${PUBLISH_TUNNEL:-false}${BI_TUNNEL:-false}" != falsefalsefalse ] && [ ! -f "$key" ]; then
  log "no SSH key at $key: create one as $AGENT_USER (ssh-keygen -t ed25519 -N '' -f $key) and add its .pub on the gateway"
fi
systemctl daemon-reload
if [ "${#enable_now[@]}" -gt 0 ]; then systemctl enable --now "${enable_now[@]}" >/dev/null; fi
# The agents' schedules stay off until each agent passed a manual run (SETUP.md).
if [ "${ENABLE_TIMERS:-false}" = "true" ]; then
  systemctl enable --now "${timers[@]}" >/dev/null
  log "timers on: ${timers[*]}"
else
  log "timers installed but off: ${timers[*]} (ENABLE_TIMERS=true to start them)"
fi

log "done. Manual tick: sudo systemctl start agent@triage; journalctl -u agent@triage -e"
