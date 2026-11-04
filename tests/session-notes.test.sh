#!/usr/bin/env bash
# Tests for plugins/core/scripts/session-notes.sh (SessionStart note: recurring lessons + open
# directives), offline: a fake curl first on PATH serves canned Plane JSON. Run: bash tests/session-notes.test.sh
set -u
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/plugins/core/scripts/session-notes.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/fx"
cat >"$T/bin/curl" <<'CURL'
#!/usr/bin/env bash
url="${!#}"; path="${url#*/api/v1/}"; path="${path%%\?*}"; f="$FX/${path//\//_}.json"
[ -f "$f" ] && cat "$f" || exit 22
CURL
chmod +x "$T/bin/curl"
export PATH="$T/bin:$PATH" FX="$T/fx" CLAUDE_PLUGIN_DATA="$T/data" SESSION_NOTES_MAX_AGE=0
export CLAUDE_PLUGIN_OPTION_TRACKER_API_KEY=k CLAUDE_PLUGIN_OPTION_TRACKER_BASE_URL=http://plane.test CLAUDE_PLUGIN_OPTION_TRACKER_WORKSPACE_SLUG=ws
W="workspaces_ws_"; fail=0
ok() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; echo "$out" | sed 's/^/     /'; fail=1; fi; }

echo '{"results":[{"id":"p1","identifier":"BIL"},{"id":"p2","identifier":"PRC"}]}' >"$FX/${W}projects_.json"
for p in p1 p2; do
  echo '{"results":[{"id":"l-les","name":"lesson"},{"id":"l-dir","name":"directive"},{"id":"l-x","name":"team:data"}]}' >"$FX/${W}projects_${p}_labels_.json"
  echo '{"results":[{"id":"s-in","name":"Inbox"},{"id":"s-td","name":"Todo"},{"id":"s-done","name":"Done"},{"id":"s-can","name":"Cancelled"}]}' >"$FX/${W}projects_${p}_states_.json"
done
item() { echo "{\"sequence_id\":$1,\"name\":\"$2\",\"state\":\"$3\",\"labels\":$4,\"created_at\":\"$5\"}"; }
echo "{\"results\":[
  $(item 1 'Lesson [paid-vs-remaining]: used remaining value as paid' s-done '["l-les"]' 2026-10-01),
  $(item 2 'Lesson [paid-vs-remaining]: paid sum from the wrong column' s-done '["l-les"]' 2026-10-05),
  $(item 3 'Lesson [unchecked-column]: invented a column name' s-done '["l-les"]' 2026-10-06),
  $(item 4 'Lesson [claimed-fix]: said fixed without a test' s-done '["l-les"]' 2026-10-02),
  $(item 5 'Lesson [claimed-fix]: reported a fix that was never made' s-done '["l-les"]' 2026-10-03),
  $(item 6 'Mechanism [claimed-fix]: done comment must cite a test' s-done '[]' 2026-10-04),
  $(item 7 'Directive: every backend task needs a handoff doc' s-td '["l-dir"]' 2026-10-07),
  $(item 8 'Directive: old rule' s-done '["l-dir"]' 2026-09-01),
  $(item 9 'Normal task' s-td '["l-x"]' 2026-10-08)
  ],\"next_page_results\":false}" >"$FX/${W}projects_p1_work-items_.json"
echo "{\"results\":[
  $(item 1 'Lesson [paid-vs-remaining]: PRC also' s-in '["l-les"]' 2026-10-09),
  $(item 2 'Mechanism [paid-vs-remaining]: warehouse-traps rule' s-td '[]' 2026-10-10)
  ],\"next_page_results\":false}" >"$FX/${W}projects_p2_work-items_.json"

out="$(bash "$SCRIPT")"
ok "recurring lesson counted across both projects, latest summary, open fix shown" '[[ "$out" == *"[paid-vs-remaining] x3: PRC also (fix in progress: PRC-2)"* ]]'
ok "single lesson not listed" '[[ "$out" != *"unchecked-column"* ]]'
ok "lesson fixed by a Done mechanism (after it) not listed" '[[ "$out" != *"claimed-fix"* ]]'
ok "open directive listed" '[[ "$out" == *"BIL-7: every backend task needs a handoff doc"* ]]'
ok "closed directive not listed" '[[ "$out" != *"old rule"* ]]'
ok "short" '[ "$(printf "%s\n" "$out" | wc -l)" -le 15 ]'

# A lesson after its mechanism was Done: the fix didn't hold → listed again
echo "{\"results\":[
  $(item 4 'Lesson [claimed-fix]: said fixed without a test' s-done '["l-les"]' 2026-10-02),
  $(item 6 'Mechanism [claimed-fix]: done comment must cite a test' s-done '[]' 2026-10-04),
  $(item 11 'Lesson [claimed-fix]: again after the rule' s-in '["l-les"]' 2026-10-20)
  ],\"next_page_results\":false}" >"$FX/${W}projects_p1_work-items_.json"
echo '{"results":[],"next_page_results":false}' >"$FX/${W}projects_p2_work-items_.json"
out="$(bash "$SCRIPT")"
ok "lesson recurring after a Done mechanism is listed again" '[[ "$out" == *"[claimed-fix] x2: again after the rule"* ]]'
ok "no directives section when none open" '[[ "$out" != *"Open team rules"* ]]'

# Nothing to say → no output; errors → no output, exit 0
echo '{"results":[],"next_page_results":false}' >"$FX/${W}projects_p1_work-items_.json"
out="$(bash "$SCRIPT")"; ok "nothing to say → empty" '[ -z "$out" ]'
rm "$FX/${W}projects_.json"; out="$(bash "$SCRIPT")"; rc=$?
ok "Plane unreachable → silent, exit 0" '[ -z "$out" ] && [ $rc -eq 0 ]'
out="$(CLAUDE_PLUGIN_OPTION_TRACKER_API_KEY= bash "$SCRIPT")"; ok "no token → silent" '[ -z "$out" ]'
exit $fail
