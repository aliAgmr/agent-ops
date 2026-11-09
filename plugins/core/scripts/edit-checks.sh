#!/usr/bin/env bash
# PostToolUse hook (Write|Edit|MultiEdit): fast, advisory checks on the file an agent just wrote,
# so it fixes a known mistake right away instead of at review. The checks are in
# plugins/core/edit-checks.tsv (examples: data traps in SQL, API-contract rules in backend code).
# A hit goes back to the agent as additional context (at most 10 lines, each with the fix); it
# never blocks, never prints anything else, and gives up quietly on any problem (the hook has a
# 5 s timeout). Files over 1 MB are skipped. Pure bash + grep + sed, like guard.sh.
set -u
set -f   # the rule globs are patterns, never file names (no expansion against the current folder)
input="$(cat)"
f="$(printf '%s' "$input" | tr '\n' ' ' | sed -n 's/.*"tool_input"[^}]*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
[ -n "$f" ] || f="$(printf '%s' "$input" | tr '\n' ' ' | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
[ -n "$f" ] && [ -f "$f" ] || exit 0
size="$(wc -c < "$f" 2>/dev/null | tr -d ' ')"
[ "${size:-0}" -le 1048576 ] || exit 0
rules="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/edit-checks.tsv"
[ -f "$rules" ] || exit 0
path="$(printf '%s' "$f" | tr '[:upper:]' '[:lower:]')"

path_matches() {  # path_matches <comma-separated globs>
  local g IFS=,
  for g in $1; do
    # shellcheck disable=SC2254
    case "$path" in $g) return 0;; esac
  done
  return 1
}

hits=""; n=0
while IFS="$(printf '\t')" read -r id globs must also unless msg; do
  case "$id" in ''|'#'*) continue;; esac
  [ -n "${msg:-}" ] || continue
  path_matches "$globs" || continue
  grep -Eiq -- "$must" "$f" 2>/dev/null || continue
  if [ "$also" != "-" ]; then grep -Eiq -- "$also" "$f" 2>/dev/null || continue; fi
  if [ "$unless" != "-" ]; then grep -Eiq -- "$unless" "$f" 2>/dev/null && continue; fi
  at="$(grep -Ein -m1 -- "$([ "$also" != "-" ] && printf '%s' "$also" || printf '%s' "$must")" "$f" 2>/dev/null | cut -d: -f1)"
  hits="${hits}- ${id}${at:+ (line $at)}: ${msg}
"
  n=$((n + 1)); [ "$n" -ge 10 ] && break
done < "$rules"
[ -n "$hits" ] || exit 0

text="agent-ops edit checks on $(basename "$f") (advisory; fix it now if it applies, or say why it doesn't):
$hits"
esc="$(printf '%s' "$text" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/	/ /g' | awk 'BEGIN{ORS="\\n"} {print}')"
printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"%s"}}\n' "$esc"
exit 0
