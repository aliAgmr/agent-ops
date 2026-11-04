#!/usr/bin/env bash
# Turns the desks/ folder of an agent-ops clone into your own workspace.
# Safe to re-run: it never overwrites STATE.md files, and keeps workspace.local.md unless --force.
# Works on macOS, Linux and Windows (Git Bash).
#
#   ./setup.sh            ask for your name, repo paths and dev commands (Enter keeps the default)
#   ./setup.sh --yes      accept every default without asking
#   ./setup.sh --force    rewrite workspace.local.md and .claude/launch.json
#
# Defaults can also come from the environment: OWNER, BACKEND_REPO, FRONTEND_REPO, DATA_REPO,
# BACKEND_DEV_CMD, FRONTEND_DEV_CMD.
set -euo pipefail

YES=0; FORCE=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    --force|-f) FORCE=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

AGENTS="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$AGENTS"
# The desks live in desks/ of the agent-ops clone; the default repo paths sit next to that clone.
REPO="$(git rev-parse --show-toplevel)"
PARENT="$(dirname "$REPO")"

ask() { # ask VAR "question" default
  local var="$1" q="$2" def="$3" ans
  if [ -n "${!var:-}" ]; then def="${!var}"; fi
  if [ "$YES" = 1 ] || [ ! -t 0 ]; then ans="$def"; else read -r -p "$q [$def]: " ans; ans="${ans:-$def}"; fi
  printf -v "$var" '%s' "${ans/#\~/$HOME}"
}
# sed replacement text: escape \ & and the | delimiter so any path or command is safe
esc() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }

echo "== desks setup in $AGENTS"

# 1. Owner and paths -> workspace.local.md
if [ -f workspace.local.md ] && [ "$FORCE" = 0 ]; then
  echo "-- workspace.local.md exists, keeping it (use --force to rewrite)"
  get() { sed -n "s/^- \`\$$1\` = \`\(.*\)\`$/\1/p" workspace.local.md; }
  BACKEND_REPO="$(get BACKEND_REPO)"; FRONTEND_REPO="$(get FRONTEND_REPO)"; DATA_REPO="$(get DATA_REPO)"
else
  ask OWNER "Your name (the desks call you 'the owner')" "$(git config user.name 2>/dev/null || whoami)"
  ask BACKEND_REPO "Path to your backend checkout" "$PARENT/backend"
  ask FRONTEND_REPO "Path to your frontend checkout" "$PARENT/frontend"
  ask DATA_REPO "Path to your data/warehouse repo checkout (or none)" "$PARENT/warehouse"
  sed -e "s|__OWNER__|$(esc "$OWNER")|" -e "s|__AGENTS__|$(esc "$AGENTS")|" -e "s|__BACKEND_REPO__|$(esc "$BACKEND_REPO")|" \
      -e "s|__FRONTEND_REPO__|$(esc "$FRONTEND_REPO")|" -e "s|__DATA_REPO__|$(esc "$DATA_REPO")|" \
      templates/workspace.local.md > workspace.local.md
  echo "-- wrote workspace.local.md for $OWNER"
fi

# 2. Preview servers for the debugging desk -> .claude/launch.json
mkdir -p .claude
if [ -f .claude/launch.json ] && [ "$FORCE" = 0 ]; then
  echo "-- .claude/launch.json exists, keeping it"
else
  ask BACKEND_DEV_CMD "Command that starts the backend in dev mode on port 3000" "npm run dev"
  ask FRONTEND_DEV_CMD "Command that starts the frontend dev server on port 5173" "npm run dev -- --port 5173"
  sed -e "s|\$BACKEND_REPO|$(esc "$BACKEND_REPO")|g" -e "s|\$FRONTEND_REPO|$(esc "$FRONTEND_REPO")|g" \
      -e "s|\$BACKEND_DEV_CMD|$(esc "$BACKEND_DEV_CMD")|g" -e "s|\$FRONTEND_DEV_CMD|$(esc "$FRONTEND_DEV_CMD")|g" \
      templates/launch.json.tmpl > .claude/launch.json
  echo "-- wrote .claude/launch.json"
fi

# 3. An empty STATE.md per desk (never overwritten)
for desk in */DESK.md; do
  d="$(dirname "$desk")"
  if [ ! -f "$d/STATE.md" ]; then
    name="$(sed -n '1s/^# \(.*\) desk.*/\1/p' "$desk")"
    sed "s|__DESK__|${name:-$d}|" templates/STATE.md > "$d/STATE.md"
    echo "-- created $d/STATE.md"
  fi
done

# 4. Commit check that blocks secrets (for the whole agent-ops clone; the path is relative to its root)
HOOKS="$(git rev-parse --show-prefix).githooks"
CURRENT="$(git config --get core.hooksPath || true)"
if [ -n "$CURRENT" ] && [ "$CURRENT" != "$HOOKS" ]; then
  echo "WARN  core.hooksPath is already '$CURRENT'; left it (the secret check is in $HOOKS)"
else
  git config core.hooksPath "$HOOKS"
  echo "-- commit check on (core.hooksPath=$HOOKS)"
fi

# 5. Claude memory for this folder (adds missing notes, never overwrites)
MEM="$HOME/.claude/projects/$(printf '%s' "$AGENTS" | sed 's/[^A-Za-z0-9]/-/g')/memory"
mkdir -p "$MEM"; touch "$MEM/MEMORY.md"
for f in claude-memory/*.md; do
  b="$(basename "$f")"; [ "$b" = MEMORY.md ] && continue
  [ -f "$MEM/$b" ] || { cp "$f" "$MEM/$b"; echo "-- memory: added $b"; }
done
while IFS= read -r line; do
  grep -qF -- "$line" "$MEM/MEMORY.md" || echo "$line" >> "$MEM/MEMORY.md"
done < claude-memory/MEMORY.md

# 6. Checks: warn only, nothing is changed
echo "== checks (fix any WARN before starting the desk that needs it)"
chk() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "WARN  $1"; fi; }
chk "backend checkout at $BACKEND_REPO (Debugging)" "[ -d '$BACKEND_REPO/.git' ]"
chk "frontend checkout at $FRONTEND_REPO (Debugging)" "[ -d '$FRONTEND_REPO/.git' ]"
[ "$DATA_REPO" = none ] || chk "data repo checkout at $DATA_REPO" "[ -d '$DATA_REPO/.git' ]"
chk "CI token ~/.ci-basic (CI Manager)" "[ -s ~/.ci-basic ]"
chk "backups folder ~/ci-backups (CI Manager)" "[ -d ~/ci-backups ]"
# Tool plugins from the agent-ops marketplace (or MCP servers of the same name in the desktop app).
for p in metabase-tools jenkins-tools; do
  chk "plugin $p" "claude plugin list 2>/dev/null | grep -q '$p@'"
done

cat <<NEXT

== next
1. In the Claude desktop app, create a local session preset named "Agents" with this folder:
   $AGENTS
2. Open a session there and say: "Start the desks from SETUP.md."
NEXT
