#!/usr/bin/env bash
# Tests for infra/bin/precheck.py (triage, analyst, release, nightly), offline: a small local HTTP
# server serves canned JSON from a temp dir, keyed by URL path (after /api/v1|v3|v4/, `/` → `_`),
# and logs every URL. The config (routing/people/projects) is a copy of the example team pointing
# its code hosts at that server. Run: bash tests/precheck.test.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PRECHECK="$ROOT/infra/bin/precheck.sh"
T="$(mktemp -d)"; mkdir -p "$T/fx" "$T/cfg"
export FX="$T/fx"
cat >"$T/server.py" <<'PY'
import http.server, socketserver, os, re, sys, urllib.parse
FX = os.environ["FX"]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        open(os.path.join(FX, "urls.log"), "a").write(self.path + "\n")
        path = re.sub(r"^.*?/api/v[1-4]/", "", self.path.split("?")[0])
        f = os.path.join(FX, path.replace("/", "_") + ".json")
        if os.path.isfile(f):
            body = open(f, "rb").read()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers(); self.wfile.write(b'{"message":"404 Not found"}')
class Server(http.server.ThreadingHTTPServer):
    # Skip HTTPServer's reverse-DNS lookup of 127.0.0.1 (socket.getfqdn): it takes ~35 s on
    # GitHub's macOS runners, longer than the wait for the port file below.
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]
s = Server(("127.0.0.1", 0), H)
open(os.path.join(os.environ["T"], "port"), "w").write(str(s.server_address[1]))
s.serve_forever()
PY
T="$T" python3 "$T/server.py" & SERVER=$!; disown "$SERVER"
trap 'kill $SERVER 2>/dev/null; rm -rf "$T"' EXIT
for _ in $(seq 1 50); do [ -s "$T/port" ] && break; sleep 0.1; done
[ -s "$T/port" ] || { echo "FAIL the fake HTTP server did not start"; exit 1; }
URL="http://127.0.0.1:$(cat "$T/port")"
# config: the example team, its code hosts on the fake server, bot users filled in
sed -e "s#https://gitlab.example.com#$URL#" -e "s#https://github.com#$URL#" "$ROOT/routing.yaml" >"$T/cfg/routing.yaml"
cp "$ROOT/projects.yaml" "$T/cfg/projects.yaml"
sed -e 's#tracker_triage_bot: *#tracker_triage_bot: triage-bot@example.com #' \
    -e 's#tracker_release_bot:#tracker_release_bot: release-bot@example.com#' \
    -e 's#tracker_nightly_bot:#tracker_nightly_bot: nightly-bot@example.com#' "$ROOT/people.template.yaml" >"$T/cfg/people.yaml"
export AGENT_OPS_CONFIG_ROOT="$T/cfg" AGENT_OPS_ENV=/dev/null
export TRACKER_BASE_URL="$URL" TRACKER_WORKSPACE="ws" TRACKER_TRIAGE_TOKEN="test-token"
unset TRIAGE_PROJECTS
# The review rules count from this time on (as a team that rolled them out on a board with history).
export PRECHECK_RULES_SINCE=2026-10-20T11:36:05
SINCE="2026-10-19T12:00:00Z"
W="workspaces_ws_"; P="${W}projects_p1_"; D="${W}projects_p2_"; S="${W}projects_p4_"   # p1 = BIL, p2 = PRC, p4 = ANL
fail=0

# approvers: sam + alex from routing.yaml/people.yaml (u-sam, u-alex); u-noor is not one
reset() {
  rm -f "$FX"/*.json
  echo '{"results":[{"id":"p1","name":"Acme Billing","identifier":"BIL"},{"id":"p2","name":"Acme Pricing","identifier":"PRC"},{"id":"p3","name":"Other","identifier":"OT"},{"id":"p4","name":"Acme Analytics","identifier":"ANL"}]}' >"$FX/${W}projects_.json"
  echo '{"results":[{"id":"s-in","name":"Inbox"},{"id":"s-tr","name":"Triaged"},{"id":"s-td","name":"Todo"},{"id":"s-ir","name":"In Review"},{"id":"s-e2e","name":"Ready for E2E"},{"id":"s-done","name":"Done"},{"id":"s-can","name":"Cancelled"}]}' >"$FX/${P}states_.json"
  echo '{"results":[{"id":"l-q","name":"question"},{"id":"l-na","name":"needs-approval"}]}' >"$FX/${P}labels_.json"
  # PRC: same state/label names, its own ids; idle unless a test says otherwise
  echo '{"results":[{"id":"d-in","name":"Inbox"},{"id":"d-tr","name":"Triaged"},{"id":"d-td","name":"Todo"},{"id":"d-ir","name":"In Review"},{"id":"d-e2e","name":"Ready for E2E"},{"id":"d-done","name":"Done"},{"id":"d-can","name":"Cancelled"}]}' >"$FX/${D}states_.json"
  echo '{"results":[{"id":"dl-q","name":"question"},{"id":"dl-na","name":"needs-approval"}]}' >"$FX/${D}labels_.json"
  prcitems '{"id":"j1","state":"d-td","labels":[],"updated_at":"2026-10-18T10:00:00Z"}'
  # ANL: same names, its own ids; approvers Kenji + Sam + Alex (routing.yaml projects.ANL)
  echo '{"results":[{"id":"b-in","name":"Inbox"},{"id":"b-td","name":"Todo"},{"id":"b-ir","name":"In Review"},{"id":"b-e2e","name":"Ready for E2E"},{"id":"b-done","name":"Done"},{"id":"b-can","name":"Cancelled"}]}' >"$FX/${S}states_.json"
  echo '{"results":[{"id":"bl-q","name":"question"}]}' >"$FX/${S}labels_.json"
  anlitems '{"id":"k1","state":"b-td","labels":[],"updated_at":"2026-10-18T10:00:00Z"}'
  echo '[{"id":"u-sam","email":"Sam@Example.com"},{"id":"u-alex","email":"alex@example.com"},{"id":"u-noor","email":"noor@example.com"},{"id":"u-kenji","email":"kenji@example.com"},{"id":"u-rel","email":"release-bot@example.com"},{"id":"u-tri","email":"triage-bot@example.com"},{"id":"u-ngt","email":"nightly-bot@example.com"}]' >"$FX/${W}members_.json"
  items '{"id":"i1","state":"s-td","labels":[],"updated_at":"2026-10-18T10:00:00Z"},{"id":"i2","state":"s-done","labels":[],"updated_at":"2026-10-19T13:00:00Z"}'
}
items() { echo "{\"results\":[$1],\"next_page_results\":false}" >"$FX/${P}work-items_.json"; }
acts() { echo "{\"results\":[$2],\"next_page_results\":false}" >"$FX/${P}work-items_$1_activities_.json"; }
prcitems() { echo "{\"results\":[$1],\"next_page_results\":false}" >"$FX/${D}work-items_.json"; }
prcacts() { echo "{\"results\":[$2],\"next_page_results\":false}" >"$FX/${D}work-items_$1_activities_.json"; }
anlitems() { echo "{\"results\":[$1],\"next_page_results\":false}" >"$FX/${S}work-items_.json"; }
anlacts() { echo "{\"results\":[$2],\"next_page_results\":false}" >"$FX/${S}work-items_$1_activities_.json"; }
comments() { echo "{\"results\":[$2]}" >"$FX/${P}work-items_$1_comments_.json"; }
removed() {  # removed <actor-json> <created_at>
  echo "{\"field\":\"labels\",\"verb\":\"updated\",\"old_value\":\"needs-approval\",\"new_value\":\"\",\"old_identifier\":\"l-na\",\"new_identifier\":null,\"actor\":$1,\"created_at\":\"$2\"}"
}
moved() {  # moved <old-id> <old-name> <new-id> <new-name> <actor-json> <created_at>
  echo "{\"field\":\"state\",\"verb\":\"updated\",\"old_value\":\"$2\",\"new_value\":\"$4\",\"old_identifier\":\"$1\",\"new_identifier\":\"$3\",\"actor\":$5,\"created_at\":\"$6\"}"
}
check() {  # check <expected-exit> <label> [text the output must contain]
  out="$(bash "$PRECHECK" triage "$SINCE" 2>&1)"; got=$?
  if [ "$got" = "$1" ] && [[ "$out" == *"${3:-}"* ]]; then echo "ok   $2 ($out)"; else echo "FAIL $2 (expected $1, got $got: $out)"; fail=1; fi
}

reset
check 1 "no changes → idle" "idle"

reset; items '{"id":"i1","state":"s-in","labels":[],"updated_at":"2026-10-18T10:00:00Z"}'
check 0 "Inbox item → work" "work: BIL:"

# Backlog items imported from another tool: only the unreviewed ones wake triage
backlog() {  # backlog <labels-json-array>
  echo '{"results":[{"id":"s-bl","name":"Backlog"},{"id":"s-in","name":"Inbox"},{"id":"s-td","name":"Todo"},{"id":"s-done","name":"Done"}]}' >"$FX/${P}states_.json"
  echo '{"results":[{"id":"l-q","name":"question"},{"id":"l-rev","name":"backlog:to-review"},{"id":"l-src","name":"source:backlog-sheet"}]}' >"$FX/${P}labels_.json"
  items "{\"id\":\"i1\",\"state\":\"s-bl\",\"labels\":$1,\"updated_at\":\"2026-10-18T10:00:00Z\"}"
}
reset; backlog '["l-src","l-rev"]'
check 0 "Backlog item to review → work" "work: BIL: 1 backlog item(s) to review"
reset; backlog '["l-src"]'
check 1 "reviewed Backlog item → idle" "idle"

reset; items '{"id":"i1","state":"s-td","labels":[],"updated_at":"2026-10-19T13:00:00Z"}'
acts i1 '{"field":"name","verb":"updated","old_value":"a","new_value":"b","actor":"u-noor","created_at":"2026-10-19T13:00:00Z"}'
check 1 "Todo item renamed → idle"

# tick step 4 (needs-approval badge) is retired: a removal by anyone no longer wakes triage
reset; items '{"id":"i1","state":"s-td","labels":[],"updated_at":"2026-10-19T13:00:00Z"}'
acts i1 "$(removed '"u-noor"' 2026-10-19T13:00:00Z)"
check 1 "needs-approval removed by non-approver → idle (badge retired)" "idle"

reset; items '{"id":"i1","state":"s-td","labels":[],"updated_at":"2026-10-19T13:00:00Z"}'
acts i1 "$(removed null 2026-10-19T13:00:00Z)"
check 1 "needs-approval removed by unknown actor → idle (badge retired)" "idle"

reset; items '{"id":"i1","state":"s-tr","labels":["l-q"],"updated_at":"2026-10-18T10:00:00Z"}'
comments i1 '{"id":"c1","created_at":"2026-10-19T14:00:00Z"}'
check 0 "open question with new comment → work"

reset; items '{"id":"i1","state":"s-tr","labels":["l-q"],"updated_at":"2026-10-18T10:00:00Z"}'
comments i1 '{"id":"c1","created_at":"2026-10-18T14:00:00Z"}'
check 1 "open question, old comment only → idle"

# tick step 6: review/Done rules (rules rolled out at 2026-10-20T11:36:05Z: PRECHECK_RULES_SINCE)
export PRECHECK_RULES_SINCE=2026-10-20T11:36:05
AFTER="2026-10-20T12:00:00.123456Z"
step6() {  # step6 <item-state> <activity-json>
  reset; items "{\"id\":\"i1\",\"state\":\"$1\",\"labels\":[],\"updated_at\":\"$AFTER\"}"; acts i1 "$2"
}
step6 s-done "$(moved s-ir "In Review" s-done Done '"u-kenji"' "$AFTER")"
check 0 "→Done by assignee after cutoff → work" "forbidden"
step6 s-done "$(moved s-e2e "Ready for E2E" s-done Done '"u-rel"' "$AFTER")"
check 1 "→Done by release bot → idle" "idle"
step6 s-done "$(moved s-ip "In Progress" s-done Done '"u-ngt"' "$AFTER")"
check 1 "→Done by nightly bot → idle" "idle"
step6 s-rfe "$(moved s-ir "In Review" s-rfe "Ready for E2E" '"u-ngt"' "$AFTER")"
check 0 "In Review→Ready for E2E by nightly bot → work (approvers only)" "forbidden"
step6 s-done "$(moved s-ir "In Review" s-done Done '"u-sam"' "$AFTER")"
check 1 "→Done by Sam → idle" "idle"
step6 s-done "$(moved s-td Todo s-done Done '"u-tri"' "$AFTER")"
check 1 "→Done by triage bot (question closed) → idle" "idle"
step6 s-done "$(moved s-ir "In Review" s-done Done '"u-kenji"' 2026-10-20T11:30:12.511Z)"
check 1 "→Done before PRECHECK_RULES_SINCE → idle" "idle"
step6 s-done "$(moved s-ir "In Review" s-done Done '"u-kenji"' 2026-10-20T11:36:05.200Z)"
check 0 "→Done just after PRECHECK_RULES_SINCE (same second) → work" "forbidden"
SINCE="2026-10-20T12:30:00Z"
step6 s-done "$(moved s-ir "In Review" s-done Done '"u-kenji"' "$AFTER")"
check 1 "→Done after PRECHECK_RULES_SINCE but before the last run → idle" "idle"
SINCE="2026-10-19T12:00:00Z"
step6 s-e2e "$(moved s-ir "In Review" s-e2e "Ready for E2E" '"u-noor"' "$AFTER")"
check 0 "In Review→Ready for E2E by assignee → work" "forbidden"
step6 s-e2e "$(moved s-ir "In Review" s-e2e "Ready for E2E" '"u-alex"' "$AFTER")"
check 1 "In Review→Ready for E2E by Alex → idle" "idle"
step6 s-e2e "$(moved s-ir "In Review" s-e2e "Ready for E2E" null "$AFTER")"
check 0 "In Review→Ready for E2E by unknown actor → work (fallback)" "unknown"
step6 s-can "$(moved s-td Todo s-can Cancelled '"u-noor"' "$AFTER")"
check 0 "→Cancelled by non-approver → work" "forbidden"
step6 s-can "$(moved s-td Todo s-can Cancelled '"u-alex"' "$AFTER")"
check 1 "→Cancelled by Alex → idle" "idle"
step6 s-ir "$(moved s-td "In Progress" s-ir "In Review" '"u-noor"' "$AFTER")"
check 1 "→In Review by assignee → idle" "idle"
step6 s-done '{"field":"state","old_value":"In Review","new_value":"Done","old_identifier":null,"new_identifier":null,"actor":"u-kenji","created_at":"2026-10-20T12:00:00Z"}'
check 0 "→Done by name only (no ids) → work" "forbidden"
reset; items "{\"id\":\"i1\",\"state\":\"s-done\",\"labels\":[],\"updated_at\":\"$AFTER\"}"
check 3 "step 6 activity endpoint error → exit 3" "activities"

unset PRECHECK_RULES_SINCE
# without PRECHECK_RULES_SINCE every change after the last run counts
step6 s-done "$(moved s-ir "In Review" s-done Done '"u-kenji"' 2026-10-20T11:30:12.511Z)"
check 0 "→Done with no PRECHECK_RULES_SINCE → work" "forbidden"
export PRECHECK_RULES_SINCE=2026-10-20T11:36:05

# second board (Acme Pricing): same rules, its own state/label ids
reset; prcitems '{"id":"j1","state":"d-in","labels":[],"updated_at":"2026-10-18T10:00:00Z"}'
check 0 "PRC Inbox item → work" "work: PRC: 1 item(s) in Inbox"
reset; prcitems '{"id":"j1","state":"s-in","labels":[],"updated_at":"2026-10-18T10:00:00Z"}'
check 1 "PRC item with a BIL state id → idle (states are per project)" "idle"
reset; prcitems "{\"id\":\"j1\",\"state\":\"d-done\",\"labels\":[],\"updated_at\":\"$AFTER\"}"
prcacts j1 "$(moved d-ir "In Review" d-done Done '"u-kenji"' "$AFTER")"
check 0 "PRC →Done by assignee → work" "work: PRC: forbidden"
reset; prcitems "{\"id\":\"j1\",\"state\":\"d-done\",\"labels\":[],\"updated_at\":\"$AFTER\"}"
prcacts j1 "$(moved d-ir "In Review" d-done Done '"u-alex"' "$AFTER")"
check 1 "PRC →Done by Alex → idle" "idle"
reset; prcitems '{"id":"j1","state":"d-td","labels":[],"updated_at":"2026-10-19T13:00:00Z"}'
prcacts j1 "$(removed '"u-noor"' 2026-10-19T13:00:00Z | sed 's/"l-na"/"dl-na"/')"
check 1 "PRC needs-approval removed by non-approver → idle (badge retired)" "idle"
reset; prcitems '{"id":"j1","state":"d-tr","labels":["dl-q"],"updated_at":"2026-10-18T10:00:00Z"}'
echo '{"results":[{"id":"c1","created_at":"2026-10-19T14:00:00Z"}]}' >"$FX/${D}work-items_j1_comments_.json"
check 0 "PRC open question with new comment → work" "work: PRC:"
# third board (ANL): Kenji is an approver there, not on BIL/PRC
reset; anlitems '{"id":"k1","state":"b-in","labels":[],"updated_at":"2026-10-18T10:00:00Z"}'
check 0 "ANL Inbox item → work" "work: ANL: 1 item(s) in Inbox"
reset; anlitems "{\"id\":\"k1\",\"state\":\"b-done\",\"labels\":[],\"updated_at\":\"$AFTER\"}"
anlacts k1 "$(moved b-ir "In Review" b-done Done '"u-kenji"' "$AFTER")"
check 1 "ANL →Done by Kenji → idle (ANL approver)" "idle"
reset; anlitems "{\"id\":\"k1\",\"state\":\"b-can\",\"labels\":[],\"updated_at\":\"$AFTER\"}"
anlacts k1 "$(moved b-td Todo b-can Cancelled '"u-noor"' "$AFTER")"
check 0 "ANL →Cancelled by a non-approver → work" "work: ANL: forbidden"
reset; items "{\"id\":\"i2\",\"state\":\"s-done\",\"labels\":[],\"updated_at\":\"$AFTER\"}"
acts i2 "$(moved s-ir "In Review" s-done Done '"u-kenji"' "$AFTER")"
check 0 "BIL →Done by Kenji → still forbidden (approvers are per project)" "work: BIL: forbidden"
reset; echo '{"results":[{"id":"p1","name":"Acme Billing","identifier":"BIL"}]}' >"$FX/${W}projects_.json"
check 3 "PRC project not visible → exit 3" "PRC not found"

# tick steps 2e/2f: effort labels (only on boards that have them)
LATE="2026-10-21T09:00:00Z"
eff() {  # eff <state> <labels-json-array> [updated_at]: one BIL item on a board with effort labels
  reset
  echo '{"results":[{"id":"s-in","name":"Inbox"},{"id":"s-td","name":"Todo"},{"id":"s-bl","name":"Backlog"},{"id":"s-qa","name":"QA"},{"id":"s-done","name":"Done"}]}' >"$FX/${P}states_.json"
  echo '{"results":[{"id":"l-q","name":"question"},{"id":"l-les","name":"lesson"},{"id":"l-rev","name":"backlog:to-review"},{"id":"e-s","name":"effort:s"},{"id":"e-m","name":"effort:m"}]}' >"$FX/${P}labels_.json"
  items "{\"id\":\"i1\",\"state\":\"$1\",\"labels\":$2,\"updated_at\":\"${3:-2026-10-18T10:00:00Z}\"}"
  acts i1 ""
}
lab() {  # lab <old> <new> <actor-json> <created_at>: a label change
  echo "{\"field\":\"labels\",\"verb\":\"updated\",\"old_value\":\"$1\",\"new_value\":\"$2\",\"actor\":$3,\"created_at\":\"$4\"}"
}
edit() {  # edit <field> <actor-json> <created_at>
  echo "{\"field\":\"$1\",\"verb\":\"updated\",\"old_value\":\"a\",\"new_value\":\"b\",\"actor\":$2,\"created_at\":\"$3\"}"
}
eff s-td '["e-s"]'
check 1 "Todo item with an effort label, no changes → idle" "idle"
eff s-td '[]'
check 0 "Todo item without an effort label → work (backfill)" "without an effort label"
eff s-bl '[]'
check 0 "reviewed Backlog item without effort → work (backfill)" "without an effort label"
eff s-bl '["l-rev"]'
check 0 "Backlog item to review → work for the review (sets effort there)" "backlog item(s) to review"
eff s-qa '[]'
check 1 "QA item without effort → idle (no effort on QA)" "idle"
eff s-done '[]'
check 1 "Done item without effort → idle" "idle"
eff s-td '["l-les"]'
check 1 "lesson without effort → idle" "idle"
eff s-td '[]'; acts i1 "$(lab effort:m "" '"u-sam"' 2026-10-18T09:00:00Z)"
check 1 "effort label removed by a person → idle (theirs to keep)" "idle"
eff s-td '[]'; acts i1 "$(lab effort:m "" '"u-tri"' 2026-10-18T09:00:00Z)"
check 0 "effort label removed by triage itself → work (backfill)" "without an effort label"
eff s-td '[]'; acts i1 "$(lab "" effort:m '"u-sam"' 2026-10-18T08:00:00Z),$(lab effort:m "" '"u-tri"' 2026-10-18T09:00:00Z)"
check 0 "latest effort change by triage counts, not an older one by a person → work" "without an effort label"
eff s-td '["e-s"]' "$LATE"; acts i1 "$(edit description '"u-noor"' "$LATE")"
check 0 "description edited by a person → work (re-assess)" "effort re-assessment"
eff s-td '["e-s"]' "$LATE"; acts i1 "$(edit name '"u-alex"' "$LATE")"
check 0 "title edited by a person → work (re-assess)" "effort re-assessment"
eff s-td '["e-s"]' "$LATE"; acts i1 "$(edit parent '"u-sam"' "$LATE")"
check 0 "child added/removed by a person → work (re-assess)" "effort re-assessment"
eff s-td '["e-s"]' "$LATE"; acts i1 "$(edit description '"u-tri"' "$LATE")"; comments i1 ""
check 1 "description edited by triage/sync → idle" "idle"
eff s-td '["e-s"]' "$LATE"; acts i1 "$(lab effort:s effort:m '"u-sam"' "$LATE")"; comments i1 ""
check 1 "effort label changed by a person → idle (no re-assess)" "idle"
eff s-td '["e-s"]' "$LATE"; comments i1 "{\"id\":\"c1\",\"actor\":\"u-noor\",\"created_at\":\"$LATE\"}"
check 0 "new comment by a person → work (re-assess)" "new comment"
eff s-td '["e-s"]' "$LATE"; comments i1 "{\"id\":\"c1\",\"actor\":\"u-tri\",\"created_at\":\"$LATE\"},{\"id\":\"c2\",\"created_by\":\"u-rel\",\"created_at\":\"$LATE\"}"
check 1 "new comments by bots only → idle" "idle"
eff s-td '["e-s"]' "$LATE"; comments i1 "{\"id\":\"c1\",\"actor\":\"u-noor\",\"created_at\":\"2026-10-18T10:00:00Z\"}"
check 1 "comment from before the last run → idle" "idle"
eff s-done '["e-s"]' "$LATE"; acts i1 "$(edit description '"u-noor"' "$LATE")"
check 1 "Done item edited → idle (no re-assess)" "idle"

# Deep analysis queue: Inbox items labelled analysis:needed are the analyst's work, not triage's
check_as() {  # check_as <agent> <expected-exit> <label> [text]
  out="$(bash "$PRECHECK" "$1" "$SINCE" 2>&1)"; got=$?
  if [ "$got" = "$2" ] && [[ "$out" == *"${4:-}"* ]]; then echo "ok   [$1] $3 ($out)"; else echo "FAIL [$1] $3 (expected $2, got $got: $out)"; fail=1; fi
}
queue() {  # queue <labels-json-array>
  echo '{"results":[{"id":"l-q","name":"question"},{"id":"l-an","name":"analysis:needed"},{"id":"l-ad","name":"analysis:done"}]}' >"$FX/${P}labels_.json"
  items "{\"id\":\"i1\",\"state\":\"s-in\",\"labels\":$1,\"updated_at\":\"2026-10-18T10:00:00Z\"}"
}
reset; queue '["l-an"]'
check_as triage 1 "Inbox item waiting for analysis → triage idle" "idle"
check_as analyst 0 "Inbox item waiting for analysis → analyst works" "work: BIL: 1 Inbox item(s) waiting for analysis"
reset; queue '["l-ad"]'
check_as triage 0 "analysed Inbox item → triage works" "work: BIL: 1 item(s) in Inbox"
check_as analyst 1 "analysed Inbox item → analyst idle" "idle"
reset; queue '[]'
check_as analyst 1 "unqueued Inbox item → analyst idle" "idle"
reset; items '{"id":"i1","state":"s-in","labels":[],"updated_at":"2026-10-18T10:00:00Z"}'
check_as analyst 1 "board without analysis labels → analyst idle" "idle"
reset; echo '{"results":[{"id":"bl-q","name":"question"},{"id":"bl-an","name":"analysis:needed"}]}' >"$FX/${S}labels_.json"
anlitems '{"id":"k1","state":"b-in","labels":["bl-an"],"updated_at":"2026-10-18T10:00:00Z"}'
check_as analyst 0 "ANL item waiting for analysis → analyst works" "work: ANL:"
reset; queue '["l-an"]'; echo '{"results":[{"id":"s-in","name":"Inbox"},{"id":"s-td","name":"Todo"}]}' >"$FX/${P}states_.json"
items '{"id":"i1","state":"s-td","labels":["l-an"],"updated_at":"2026-10-18T10:00:00Z"}'
check_as analyst 1 "label left on a Todo item → analyst idle" "idle"

# Release: MRs/PRs into main on every release repo in routing.yaml (all code hosts), skipping
# `release: false`. GitLab paths are URL-encoded (<group>%2F<repo>); GitHub uses /repos/<owner>/<repo>/pulls.
export CODEHOST_MAIN_TOKEN="test-gl-token" CODEHOST_OSS_TOKEN="test-gh-token"
mrs() {  # mrs <gitlab repo path> <json array>
  echo "$2" >"$FX/projects_${1//\//%2F}_merge_requests.json"
}
prs() {  # prs <github owner/repo> <json array>
  echo "$2" >"$FX/repos_${1//\//_}_pulls.json"
}
GL="acme/billing-backend acme/billing-frontend acme/pricing"; GH="acme/warehouse"
release_reset() { rm -f "$FX"/*.json "$FX/urls.log"; for r in $GL; do mrs "$r" '[]'; done; for r in $GH; do prs "$r" '[]'; done; }

release_reset
check_as release 1 "no MRs/PRs into main changed → release idle" "idle"
if [ "$(grep -c 'target_branch=main' "$FX/urls.log")" = 3 ] && grep -q "updated_after=${SINCE//:/%3A}" "$FX/urls.log" \
   && [ "$(grep -c 'base=main' "$FX/urls.log")" = 1 ] && [ "$(wc -l <"$FX/urls.log" | tr -d ' ')" = 4 ]; then
  echo "ok   [release] one call per release repo (3 GitLab, 1 GitHub), into main, since the last run"
else echo "FAIL [release] unexpected calls: $(tr '\n' ' ' <"$FX/urls.log")"; fail=1; fi
grep -q 'pricing-admin' "$FX/urls.log" && { echo "FAIL [release] asked about a release: false repo"; fail=1; } \
  || echo "ok   [release] skips release: false repos"
release_reset; mrs acme/pricing '[{"iid":7,"target_branch":"main","state":"merged","updated_at":"2026-10-19T13:00:00Z"}]'
check_as release 0 "GitLab MR into main changed → release works" "work: 1 MR(s) into main changed in acme/pricing"
release_reset; prs acme/warehouse '[{"number":3,"state":"open","base":{"ref":"main"},"head":{"ref":"x","sha":"abc"},"updated_at":"2026-10-19T13:00:00Z"}]'
check_as release 0 "GitHub PR into main changed → release works" "work: 1 MR(s) into main changed in acme/warehouse"
release_reset; prs acme/warehouse '[{"number":3,"state":"open","base":{"ref":"main"},"head":{"ref":"x","sha":"abc"},"updated_at":"2026-10-18T13:00:00Z"}]'
check_as release 1 "GitHub PR not updated since the last run → idle" "idle"
release_reset; rm -f "$FX/projects_acme%2Fpricing_merge_requests.json"
check_as release 3 "code host error → check failed (run anyway)" "precheck failed"
release_reset; mrs acme/pricing '{"message":"401 Unauthorized"}'
check_as release 3 "unexpected code host answer → check failed (run anyway)" "precheck failed"
( unset CODEHOST_OSS_TOKEN; out="$(bash "$PRECHECK" release "$SINCE" 2>&1)"; got=$?
  [ "$got" = 3 ] && [[ "$out" == *"CODEHOST_OSS_TOKEN is not set"* ]] ) \
  && echo "ok   [release] missing token → check failed (3), never idle" \
  || { echo "FAIL [release] missing token should exit 3"; fail=1; }
( unset TRACKER_TRIAGE_TOKEN; out="$(bash "$PRECHECK" triage "$SINCE" 2>&1)"; got=$?
  [ "$got" = 3 ] && [[ "$out" == *"TRACKER_TRIAGE_TOKEN is not set"* ]] ) \
  && echo "ok   [triage] missing token → check failed (3), never idle" \
  || { echo "FAIL [triage] missing token should exit 3"; fail=1; }

# Nightly: scheduled by nightly.sh, so the precheck always says work (no network calls).
rm -f "$FX/urls.log"
check_as nightly 0 "nightly → always work" "work: nightly pass"
[ ! -s "$FX/urls.log" ] && echo "ok   [nightly] no network calls" || { echo "FAIL [nightly] made calls"; fail=1; }
check_as bogus 3 "unknown agent → check failed" "unknown agent"

exit $fail
