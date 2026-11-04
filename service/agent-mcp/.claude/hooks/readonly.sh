#!/usr/bin/env bash
# PreToolUse hook of the agent-mcp answering agent, on top of plugins/core/scripts/guard.sh:
#   - Plane: read actions only (list/retrieve/search/count/...); no creates, updates, comments.
#   - Read/Grep/Glob: never the host's secret locations (env file, tokens, ssh keys, Claude login,
#     Plane's plane.env, the agent-mcp database), whatever the path spelling.
# Exit 2 = block (stderr goes to the agent). Pure bash + sed + grep.
set -u
input="$(cat | tr '\n' ' ')"
tool="$(printf '%s' "$input" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
[ -z "$tool" ] && exit 0
short="${tool##*__}"

block() { echo "BLOCKED by agent-mcp read-only hook: $1. You only explain; the asker's agent does the work." >&2; exit 2; }

case "$tool" in
  mcp__plane__*|mcp__*_plane__*)
    action="$(printf '%s' "$input" | sed -n 's/.*"action"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
    if [ -n "$action" ]; then
      printf '%s' "$action" | grep -Eq '^(list|list_[a-z_]+|retrieve|retrieve_[a-z_]+|get_[a-z_]+|search|search_[a-z_]+|count|read|me|worklog_summary)$' \
        || block "Plane action '$action' is a write"
    else
      printf '%s' "$short" | grep -Eq '^(list|get|retrieve|search)_' || block "Plane tool '$short' may write"
    fi;;
esac

case "$tool" in
  Read|Grep|Glob|NotebookRead)
    home="${HOME:-/Users/nobody}"
    paths="$(printf '%s' "$input" | grep -Eo '"(file_path|path|notebook_path|pattern)"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*:[[:space:]]*"\(.*\)"/\1/')"
    while IFS= read -r p; do
      [ -z "$p" ] && continue
      case "$p" in "~"*) p="$home${p#\~}";; esac
      real="$p"
      d="$(dirname "$p")"; [ -d "$p" ] && d="$p"
      if r="$(cd "$d" 2>/dev/null && pwd -P)"; then [ -d "$p" ] && real="$r" || real="$r/$(basename "$p")"; fi
      for x in "$p" "$real"; do
        case "$x" in
          */.config/agent-ops*|*/.ssh*|*/.claude/.credentials*|*/.claude.json*|*/plane-selfhost*|\
          */.local/share/agent-mcp*|*/.docker/config*|*/.netrc|*/.gnupg*|*/Library/Keychains*|\
          *.token|*/plane.env|/etc/*|/private/etc/*|*/.aws/*|*/.kube/*|*/.git-credentials)
            block "reading '$p' is not allowed (secrets live there)";;
        esac
      done
    done <<< "$paths"
    ;;
esac
exit 0
