#!/usr/bin/env bash
# Agent host setup on macOS (the counterpart of infra/bootstrap.sh on Linux): the service agents
# run as launchd user agents of the logged-in user (Docker Desktop and launchd user agents need a
# logged-in session). Idempotent: re-run it after changing any setting below. SETUP.md runs it.
# Needs /srv → a folder in your home first:  sudo bash infra/macos/srv-link.sh "$HOME/agent-host"
#
#   bash infra/macos/install.sh                       # clones, env file, launchd agents (not loaded)
#   ENABLE_TIMERS=true bash infra/macos/install.sh    # also load the agents' schedules
#
# Settings (environment variables; all optional):
#   AGENTS="triage:300 analyst:600 release:900"   which interval agents to run, every N seconds
#   LABEL_PREFIX=io.agent-ops                     launchd label prefix (<prefix>.agent-<name>)
#   AGENT_OPS_SRC                                 where /srv/agent-ops is cloned from (default: this checkout)
#   TRACKER_BASE_URL, TRACKER_WORKSPACE           written into the env file when given
#   NIGHTLY=true NIGHTLY_AT=02:30 NIGHTLY_DAYS=1,2,3,4,5 NIGHTLY_SEND_AT=08:30 NIGHTLY_SEND_DAYS=1,2,3,4,5
#                                                 the nightly pass and its messages (host's local time;
#                                                 days: 0=Sunday … 6=Saturday)
#   WAKE=true                                     instant triage from task-board webhooks (wake.py, :8766)
#   AGENT_MCP=true                                the project Q&A + action journal server (127.0.0.1:8765)
#   JENKINS_WATCH=true JENKINS_WATCH_INTERVAL=1800 the Jenkins watcher agent (follows ENABLE_TIMERS) and its
#                                                 Telegram poller (every 60 s, loaded at once;
#                                                 infra/jenkins-watch/README.md)
#   BACKUP=true BACKUP_AT=03:15 OFFSITE_DIR=…     nightly backup of the task board, n8n and the agents' config
#   TUNNEL=true TUNNEL_TARGET=… TUNNEL_FORWARDS=… SSH local forwards to data sources (tunnel.sh)
#   PUBLISH_TUNNEL=true TUNNEL_TARGET=… PUBLISH_PORT=8090 PUBLISH_LOCAL_PORT=80
#                                                 publish the task board on a gateway (publish-tunnel.sh)
#   BI_TUNNEL=true BI_REMOTE_PORT=15435 BI_LOCAL_PORT=15432 BI_BIND=0.0.0.0
#                                                 a second publish tunnel for BI → the task board's database
set -euo pipefail
[ "$(uname)" = Darwin ] || { echo "macOS only; on Linux use infra/bootstrap.sh" >&2; exit 1; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC_ROOT="$(cd "$HERE/../.." && pwd)"
CONF="$HOME/.config/agent-ops"
ENV_FILE="$CONF/env"
LAUNCH="$HOME/Library/LaunchAgents"
PREFIX="${LABEL_PREFIX:-io.agent-ops}"
DOMAIN="gui/$(id -u)"
log() { echo "[macos] $*"; }

# 1. Tools
for c in git jq claude uv uvx npx; do
  command -v "$c" >/dev/null || { echo "missing $c (brew install git jq uv node; npm install -g @anthropic-ai/claude-code)" >&2; exit 1; }
done
[ -L /srv ] && [ -d /srv/ ] || { echo "/srv is missing: sudo bash $HERE/srv-link.sh \"$HOME/agent-host\"" >&2; exit 1; }

# 2. Env file (names in infra/env.template). Created once; never printed. Fill the empty secrets
# with SETUP.md's hidden-input commands, then re-run this script to clone the repos.
mkdir -p "$CONF" && chmod 700 "$CONF"
if [ ! -f "$ENV_FILE" ]; then
  log "creating $ENV_FILE from env.template"
  grep -E '^[A-Z0-9_]+=' "$SRC_ROOT/infra/env.template" > "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"
set_env() {  # key value
  local tmp; tmp="$(mktemp)"
  awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v; f=1} {print} END{if(!f) print k"="v}' "$ENV_FILE" > "$tmp"
  cat "$tmp" > "$ENV_FILE"; rm -f "$tmp"
}
[ -z "${TRACKER_BASE_URL:-}" ] || set_env TRACKER_BASE_URL "$TRACKER_BASE_URL"
[ -z "${TRACKER_WORKSPACE:-}" ] || set_env TRACKER_WORKSPACE "$TRACKER_WORKSPACE"
missing="$(awk -F= '$2=="" || $2=="\"\"" {print $1}' "$ENV_FILE" | tr '\n' ' ')"
[ -n "$missing" ] && log "still empty in $ENV_FILE (fine if you don't use them): $missing"

# 3. Deploy checkout + read-only clones of every repo in routing.yaml. The clones fetch from the
# code host with the agents' read-only tokens (infra/bin/repo-sync.sh), so the agents see the code
# host, not a local working copy. On a first install, fill the tokens and run this again.
mkdir -p /srv/repos
if [ ! -d /srv/agent-ops/.git ]; then
  log "clone agent-ops → /srv/agent-ops"; git clone -q "${AGENT_OPS_SRC:-$SRC_ROOT}" /srv/agent-ops
fi
repo_sync() { ( set -a; . "$ENV_FILE"; set +a; bash "$SRC_ROOT/infra/bin/repo-sync.sh" "$@" ); }
# routing.yaml → "<clone url> <dir name>" per repo (code_hosts[host].url + path), each repo once.
repos="$(uv run -q --no-project --with pyyaml==6.0.2 python - "$SRC_ROOT/routing.yaml" <<'PY'
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
  if [ -d "/srv/repos/$name/.git" ]; then
    repo_sync pull "/srv/repos/$name" >/dev/null 2>&1 || log "pull $name failed (token in $ENV_FILE?)"
  else
    log "clone $name"
    repo_sync clone "$url" "/srv/repos/$name" || log "clone $name failed: code host token in $ENV_FILE? (re-run this script)"
  fi
done <<< "$repos"

# Service agents' MCP configs (service/*/.mcp.json, git-ignored) from their templates + routing.yaml;
# run-agent.sh re-renders before every tick, this covers agent-mcp and a first manual run.
AGENT_OPS_ENV="$ENV_FILE" python3 -I /srv/agent-ops/infra/bin/render-mcp.py all >/dev/null \
  || log "render-mcp.py all failed (routing.yaml / env file?); re-run this script after fixing"

# 4. Warm the MCP package caches so the first tick doesn't time out downloading them (stdin is
# closed, so each server exits right after starting).
log "warming MCP caches (first time takes a few minutes)"
uvx --from plane-mcp-server==0.3.4 plane-mcp-server stdio </dev/null >/dev/null 2>&1 || true
uv run --with mcp-clickhouse --python 3.10 python -c 'import mcp_clickhouse' >/dev/null 2>&1 || true
for p in @zereight/mcp-gitlab @microsoft/postgres-mcp mcp-remote figma-developer-mcp; do npm cache add "$p" >/dev/null 2>&1 || true; done

# Renders a template into ~/Library/LaunchAgents, checks it and (re)loads it.
#   load <template> <job> [sed expressions…]
load() {
  local tpl="$1" job="$2"; shift 2
  local plist="$LAUNCH/$PREFIX.$job.plist"
  mkdir -p "$LAUNCH" "$HOME/Library/Logs/agent-ops/${job#agent-}"
  sed -e "s|__HOME__|$HOME|g" -e "s|__PREFIX__|$PREFIX|g" -e "s|__JOB__|$job|g" "$@" "$tpl" > "$plist"
  plutil -lint -s "$plist"
  launchctl bootout "$DOMAIN/$PREFIX.$job" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$plist" && log "loaded $PREFIX.$job"
}
# "HH:MM" + "0,1,2" → launchd StartCalendarInterval <dict> entries (one per weekday; every day if empty)
calendar() {
  local at="$1" days="$2" h m out="" d
  h="$((10#${at%%:*}))"; m="$((10#${at##*:}))"
  if [ -z "$days" ]; then
    printf '<dict><key>Hour</key><integer>%s</integer><key>Minute</key><integer>%s</integer></dict>' "$h" "$m"; return
  fi
  for d in ${days//,/ }; do
    out+="<dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>$h</integer><key>Minute</key><integer>$m</integer></dict>"
  done
  printf '%s' "$out"
}

# 5. Interval agents (systemd: agent-<name>.timer). Schedules stay off until each agent passed its
# manual test run (SETUP.md); ENABLE_TIMERS=true loads them. JENKINS_WATCH=true adds the Jenkins watcher.
agent_specs="${AGENTS:-triage:300 analyst:600 release:900}"
if [ "${JENKINS_WATCH:-false}" = "true" ]; then agent_specs="$agent_specs jenkins-watch:${JENKINS_WATCH_INTERVAL:-1800}"; fi
for spec in $agent_specs; do
  a="${spec%%:*}"; interval="${spec##*:}"
  if [ "${ENABLE_TIMERS:-false}" = "true" ]; then
    load "$HERE/agent.plist.template" "agent-$a" -e "s|__AGENT__|$a|g" -e "s|__INTERVAL__|$interval|g"
  else
    mkdir -p "$LAUNCH" "$HOME/Library/Logs/agent-ops/$a"
    sed -e "s|__HOME__|$HOME|g" -e "s|__PREFIX__|$PREFIX|g" -e "s|__AGENT__|$a|g" -e "s|__INTERVAL__|$interval|g" \
      "$HERE/agent.plist.template" > "$LAUNCH/$PREFIX.agent-$a.plist"
    plutil -lint -s "$LAUNCH/$PREFIX.agent-$a.plist"
    log "rendered $PREFIX.agent-$a (every ${interval}s; not loaded: ENABLE_TIMERS=true loads it)"
  fi
done

# 6. Nightly pass and its messages.
if [ "${NIGHTLY:-false}" = "true" ]; then
  mkdir -p /srv/nightly && chmod 700 /srv/nightly
  load "$HERE/nightly.plist.template" nightly -e "s|__CALENDAR__|$(calendar "${NIGHTLY_AT:-02:30}" "${NIGHTLY_DAYS:-1,2,3,4,5}")|"
  load "$HERE/nightly-send.plist.template" nightly-send -e "s|__CALENDAR__|$(calendar "${NIGHTLY_SEND_AT:-08:30}" "${NIGHTLY_SEND_DAYS:-1,2,3,4,5}")|"
fi

# 7. Instant triage: wake.py kickstarts an agent when n8n forwards a task-board change.
if [ "${WAKE:-false}" = "true" ]; then
  load "$HERE/wake.plist.template" agent-wake
fi

# 8. Project Q&A + action journal server for teammates' agents (reached through your reverse proxy
# at AGENT_MCP_PUBLIC_URL; infra/agent-mcp/README.md).
if [ "${AGENT_MCP:-false}" = "true" ]; then
  load "$SRC_ROOT/infra/agent-mcp/agent-mcp.plist.template" agent-mcp
fi

# 8b. Jenkins watcher's Telegram poller (every 60 s): keeps the watched topic's messages, and lists the
# groups and topics the bot sees (`python3 infra/telegram/poll.py chats`) for TELEGRAM_WATCH_CHAT_ID /
# TELEGRAM_WATCH_TOPIC_ID. Loaded at once, so the ids can be found before the agent's schedule is on.
if [ "${JENKINS_WATCH:-false}" = "true" ]; then
  load "$HERE/telegram-poll.plist.template" telegram-poll
fi

# 9. Nightly backup of the task board, n8n and the agents' config (restore: infra/macos/README.md).
if [ "${BACKUP:-false}" = "true" ]; then
  load "$HERE/backup.plist.template" agent-backup -e "s|__OFFSITE__|${OFFSITE_DIR:-}|g" \
    -e "s|__CALENDAR__|$(calendar "${BACKUP_AT:-03:15}" "")|"
fi

# 10. Optional SSH tunnels (need TUNNEL_TARGET=user@host; key passphrase in the Keychain first:
# ssh-add --apple-use-keychain <key>).
tunnel_sed=(-e "s|__KEY__|${TUNNEL_KEY:-$HOME/.ssh/id_ed25519}|g" -e "s|__TARGET__|${TUNNEL_TARGET:-}|g"
            -e "s|__TARGET_PORT__|${TUNNEL_TARGET_PORT:-22}|g" -e "s|__JUMP__|${TUNNEL_JUMP:-}|g")
if [ "${TUNNEL:-false}" = "true" ]; then
  [ -n "${TUNNEL_TARGET:-}" ] && [ -n "${TUNNEL_FORWARDS:-}" ] || { echo "TUNNEL=true needs TUNNEL_TARGET and TUNNEL_FORWARDS" >&2; exit 1; }
  load "$HERE/tunnel.plist.template" agent-tunnel "${tunnel_sed[@]}" -e "s|__FORWARDS__|$TUNNEL_FORWARDS|g"
fi
if [ "${PUBLISH_TUNNEL:-false}" = "true" ]; then
  [ -n "${TUNNEL_TARGET:-}" ] || { echo "PUBLISH_TUNNEL=true needs TUNNEL_TARGET" >&2; exit 1; }
  load "$HERE/publish-tunnel.plist.template" publish-tunnel "${tunnel_sed[@]}" \
    -e "s|__BIND__|${PUBLISH_BIND:-0.0.0.0}|g" -e "s|__REMOTE_PORT__|${PUBLISH_PORT:-8090}|g" \
    -e "s|__LOCAL_HOST__|localhost|g" -e "s|__LOCAL_PORT__|${PUBLISH_LOCAL_PORT:-80}|g"
fi
if [ "${BI_TUNNEL:-false}" = "true" ]; then
  [ -n "${TUNNEL_TARGET:-}" ] || { echo "BI_TUNNEL=true needs TUNNEL_TARGET" >&2; exit 1; }
  load "$HERE/publish-tunnel.plist.template" bi-tunnel "${tunnel_sed[@]}" \
    -e "s|__BIND__|${BI_BIND:-0.0.0.0}|g" -e "s|__REMOTE_PORT__|${BI_REMOTE_PORT:-15435}|g" \
    -e "s|__LOCAL_HOST__|127.0.0.1|g" -e "s|__LOCAL_PORT__|${BI_LOCAL_PORT:-15432}|g"
fi

log "done. Manual tick: AGENT_OPS_ENV=$ENV_FILE bash /srv/agent-ops/infra/bin/run-agent.sh triage"
