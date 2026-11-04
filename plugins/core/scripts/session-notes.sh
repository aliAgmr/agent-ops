#!/usr/bin/env bash
# SessionStart hook: a short note for the agent with the team's recurring mistakes (Plane items
# labelled `lesson`, see the `lessons` skill) and open owner rules (`directive`). It's printed into
# the session's context. At most ~15 lines; nothing at all when there's nothing to say.
#
# Reads the task board (Plane) with the person's own token: Claude Code passes the core plugin's
# options as CLAUDE_PLUGIN_OPTION_*; the Cursor/Codex/Kimi installer sets the same variables.
# Projects: every project the token is a member of, or only SESSION_NOTES_PROJECTS ("BIL PRC"). The answer is cached
# for an hour, so most sessions start without a network call. It never fails a session: any
# error → no output, exit 0.
set -u
KEY="${CLAUDE_PLUGIN_OPTION_TRACKER_API_KEY:-}"
BASE="${CLAUDE_PLUGIN_OPTION_TRACKER_BASE_URL:-}"
WS="${CLAUDE_PLUGIN_OPTION_TRACKER_WORKSPACE_SLUG:-}"
CACHE_DIR="${CLAUDE_PLUGIN_DATA:-$HOME/.cache/agent-ops}"
CACHE="$CACHE_DIR/session-notes.txt"
MAX_AGE="${SESSION_NOTES_MAX_AGE:-3600}"
[ -n "$KEY" ] && [ -n "$BASE" ] && [ -n "$WS" ] || exit 0
command -v jq >/dev/null 2>&1 && command -v curl >/dev/null 2>&1 || exit 0

if [ -f "$CACHE" ]; then
  age=$(( $(date +%s) - $(date -r "$CACHE" +%s 2>/dev/null || stat -c %Y "$CACHE" 2>/dev/null || echo 0) ))
  if [ "$age" -lt "$MAX_AGE" ]; then cat "$CACHE"; exit 0; fi
fi

API="$BASE/api/v1/workspaces/$WS"
get() { curl -fsS -m 8 -H "X-API-Key: $KEY" "$API$1" 2>/dev/null; }
rows() { jq -c 'if type == "object" and has("results") then .results else . end'; }
items() {  # all work items of one project
  local out="[]" cursor="" page
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    page="$(get "/projects/$1/work-items/?per_page=100${cursor:+&cursor=$cursor}")" || return 1
    out="$(jq -c --argjson acc "$out" '$acc + (if type == "object" then .results else . end)' <<<"$page")" || return 1
    [ "$(jq -r '.next_page_results // false' <<<"$page")" = true ] || break
    cursor="$(jq -r '.next_cursor' <<<"$page")"
  done
  printf '%s' "$out"
}

projects="$(get /projects/ | rows)" || exit 0
all="[]"
codes="${SESSION_NOTES_PROJECTS:-$(jq -r '.[] | select(.is_member != false) | .identifier' <<<"$projects" 2>/dev/null | tr '\n' ' ')}"
for code in $codes; do
  pid="$(jq -r --arg c "$code" '.[] | select(.identifier == $c) | .id' <<<"$projects" 2>/dev/null)"
  [ -n "$pid" ] || continue
  labels="$(get "/projects/$pid/labels/" | rows)" || exit 0
  states="$(get "/projects/$pid/states/" | rows)" || exit 0
  its="$(items "$pid")" || exit 0
  all="$(jq -c --argjson acc "$all" --arg code "$code" --argjson labels "$labels" --argjson states "$states" '
    ($labels | map({(.id): .name}) | add // {}) as $ln | ($states | map({(.id): .name}) | add // {}) as $sn |
    $acc + [ .[] | {key: "\($code)-\(.sequence_id)", name, created_at,
                    state: ($sn[.state] // ""), labels: [(.labels // [])[] | $ln[.] // empty]} ]' <<<"$its")" || exit 0
done

note="$(jq -r '
  def slug: (capture("^(Lesson|Mechanism) \\[(?<s>[^\\]]+)\\]") .s) // null;
  def open: (.state != "Done" and .state != "Cancelled");
  ([ .[] | select(.labels | index("lesson")) | select(.name | test("^Lesson \\[")) ] | group_by(.name | slug)) as $groups |
  ([ .[] | select(.name | test("^Mechanism \\[")) ]) as $mech |
  [ $groups[] | select(length >= 2) | (.[0].name | slug) as $s | (sort_by(.created_at) | last) as $last |
    ([ $mech[] | select((.name | slug) == $s) ] | sort_by(.created_at) | last) as $m |
    select($m == null or ($m | open) or ($m.created_at < $last.created_at)) |
    "- [\($s)] x\(length): \($last.name | sub("^Lesson \\[[^\\]]+\\]: *"; ""))" +
      (if $m != null and ($m | open) then " (fix in progress: \($m.key))" else "" end) ] as $lessons |
  [ .[] | select(.labels | index("directive")) | select(open) | "- \(.key): \(.name | sub("^Directive: *"; ""))" ] as $dirs |
  (if ($lessons | length) > 0 then ["Recurring agent mistakes on this team (from Plane `lesson` items). Check these before answering:"] + $lessons[:6] else [] end) +
  (if ($dirs | length) > 0 then ["Open team rules from the approvers (`directive` items). Follow them even before they are built into the plugins:"] + $dirs[:6] else [] end) |
  join("\n")' <<<"$all")" || exit 0

mkdir -p "$CACHE_DIR" 2>/dev/null && printf '%s' "${note:+$note
}" > "$CACHE" 2>/dev/null
[ -n "$note" ] && printf '%s\n' "$note"
exit 0
