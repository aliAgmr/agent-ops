#!/usr/bin/env bash
# PreToolUse / PostToolUse hook: reports each write a tool call makes to the team's action journal
# (agent-mcp's /journal, infra/journal/journal.py), so every change has an author and an undo.
#   journal.sh pre    before the call: agent-mcp saves the item as it is (for a revert)
#   journal.sh post   after the call:  agent-mcp records who did what, and the result
# Reads go nowhere: only calls that look like writes are sent (agent-mcp decides for sure).
#
# Never blocks or slows a tool call for long, never prints anything, always exits 0. If agent-mcp
# can't be reached, `post` payloads wait in a spool file and go with the next successful one.
#
# Identity: the person's task board token (core plugin option, same as for agent-mcp); the URL is
# the core plugin's agent_mcp_url + /journal (or AGENT_JOURNAL_URL). Service agents
# get AGENT_JOURNAL_TOKEN / AGENT_NAME / AGENT_RUN_ID / AGENT_JOURNAL_URL from run-agent.sh.
# Pure bash + curl, like guard.sh.
set -u
phase="${1:-post}"
case "$phase" in pre|post) ;; *) exit 0;; esac
input="$(cat)"

tool="$(printf '%s' "$input" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
[ -n "$tool" ] || exit 0
short="${tool##*__}"
lower="$(printf '%s' "$input" | tr '[:upper:]' '[:lower:]')"
action="$(printf '%s' "$lower" | sed -n 's/.*"tool_input"[^}]*"action"[[:space:]]*:[[:space:]]*"\([a-z_]*\)".*/\1/p' | head -n1)"

# Quick filter: is this possibly a write? (journal.py's classify() is the real rule.)
is_write() {
  case "$tool" in
    mcp__*plane*)
      [ -n "$action" ] || return 1
      case "$action" in list*|retrieve*|get*|search*|count*|me) return 1;; esac; return 0;;
    mcp__*gitlab*|mcp__*metabase*|mcp__*figma*)
      printf '%s' "$short" | grep -Eiq '^(create|update|delete|merge|approve|play|retry|cancel|erase|resolve|stop|take_ownership|trigger|publish|upload|add|send|set|use_figma|generate|archive|remove|manage|batch|bulk|modify|post|push|edit|move|rename|close|reopen|assign|unassign)';;
    mcp__*jenkins*)
      case "$short" in triggerBuild|rebuildBuild|replayBuild|updateBuild) return 0;; esac; return 1;;
    mcp__*agent-mcp*|mcp__*agent_mcp*)
      [ "$short" = send_message ];;
    Artifact)
      case "$action" in list|read|quickstart|open) return 1;; esac; return 0;;
    ArtifactData|ArtifactComments)
      case "$action" in ''|get|list|query|read) return 1;; esac; return 0;;
    Bash)
      printf '%s' "$lower" | grep -Eq 'git[[:space:]]+(-c[[:space:]]+[^[:space:]]+[[:space:]]+)?(push|commit|revert|merge|rebase|reset|tag)([[:space:]]|")|(glab|gh)[[:space:]]+(mr|pr|issue|release|api)|curl[^|;&]*(-x[[:space:]]*.?(post|put|patch|delete)|--data|[[:space:]]-d[[:space:]]|[[:space:]]-f[[:space:]]|--form)|(^|[^a-z])(ssh|scp|rsync)[[:space:]]|docker[[:space:]]+(run|rm|stop|compose|exec)';;
    *) return 1;;
  esac
}
is_write || exit 0

MCP_BASE="${CLAUDE_PLUGIN_OPTION_AGENT_MCP_URL:-}"
URL="${AGENT_JOURNAL_URL:-${MCP_BASE:+${MCP_BASE%/}/journal}}"
[ -n "$URL" ] || exit 0
TOKEN="${AGENT_JOURNAL_TOKEN:-${CLAUDE_PLUGIN_OPTION_TRACKER_API_KEY:-}}"
[ -n "$TOKEN" ] || exit 0
command -v curl >/dev/null 2>&1 || exit 0
SPOOL_DIR="${CLAUDE_PLUGIN_DATA:-$HOME/.cache/agent-ops}"
SPOOL="$SPOOL_DIR/journal-spool.jsonl"
clean() { printf '%s' "${1:-}" | tr -cd 'A-Za-z0-9_.:-' | cut -c1-64; }
ROLE="$(clean "${CLAUDE_PLUGIN_OPTION_ROLE:-${AGENT_ROLE:-}}")"
NAME="$(clean "${AGENT_NAME:-}")"
RUN="$(clean "${AGENT_RUN_ID:-}")"
CLIENT="$(clean "${AGENT_JOURNAL_CLIENT:-claude-code}")"

# The token goes in through a curl config read from a pipe, so it never shows in `ps`.
post() {  # post <timeout> <path> <phase> <body-file>
  curl -fsS -m "$1" -o /dev/null -X POST -K <(printf 'header = "Authorization: Bearer %s"\n' "$TOKEN") \
    -H "Content-Type: application/json" -H "X-Journal-Phase: $3" -H "X-Agent-Role: $ROLE" \
    -H "X-Agent-Name: $NAME" -H "X-Agent-Run: $RUN" -H "X-Agent-Client: $CLIENT" \
    --data-binary "@$4" "$URL$2" 2>/dev/null
}

tmp="$(mktemp "${TMPDIR:-/tmp}/journal.XXXXXX" 2>/dev/null)" || exit 0
printf '%s' "$input" > "$tmp"

if [ "$phase" = pre ]; then
  # Synchronous: agent-mcp must read the item before the write happens. Short timeout.
  post "${AGENT_JOURNAL_PRE_TIMEOUT:-4}" "" pre "$tmp"
  rm -f "$tmp"; exit 0
fi

# post: in the background so the agent isn't kept waiting.
(
  if post "${AGENT_JOURNAL_POST_TIMEOUT:-8}" "" post "$tmp"; then
    if [ -s "$SPOOL" ] && mv "$SPOOL" "$SPOOL.sending" 2>/dev/null; then
      post 20 /batch batch "$SPOOL.sending" || cat "$SPOOL.sending" >> "$SPOOL"
      rm -f "$SPOOL.sending"
    fi
  else
    mkdir -p "$SPOOL_DIR" 2>/dev/null
    size="$(wc -c < "$SPOOL" 2>/dev/null || echo 0)"
    if [ "${size:-0}" -lt 5000000 ]; then
      { printf '{"phase":"post","role":"%s","agent":"%s","run":"%s","client":"%s","spooled_at":%s,"payload":' \
          "$ROLE" "$NAME" "$RUN" "$CLIENT" "$(date +%s)"
        tr -d '\n' < "$tmp"; printf '}\n'; } >> "$SPOOL" 2>/dev/null
    fi
  fi
  rm -f "$tmp"
) </dev/null >/dev/null 2>&1 &
exit 0
