#!/usr/bin/env bash
# Tests for infra/bin/post-commit-status.sh on GitLab and GitHub: it doesn't post a release-agent
# status that is already there (retry after a timeout), and still posts when the state changes or
# the lookup fails. Offline: a small local HTTP server stands in for both code hosts.
# Run: bash tests/post-commit-status.test.sh
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; mkdir -p "$T/cfg"
export AGENT_JOURNAL_DB="$T/journal.sqlite3"   # never the real action journal
cat >"$T/server.py" <<'PY'
import http.server, json, os
T = os.environ["T"]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        f = os.path.join(T, "statuses.json")
        if "/statuses" in self.path and os.path.isfile(f):
            self.send_response(200); self.end_headers(); self.wfile.write(open(f, "rb").read())
        else:
            self.send_response(500); self.end_headers(); self.wfile.write(b'{"message":"boom"}')
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode()
        auth = self.headers.get("PRIVATE-TOKEN") or self.headers.get("Authorization") or ""
        open(os.path.join(T, "posts"), "a").write(f"{self.path} {auth} {body}\n")
        self.send_response(201); self.end_headers(); self.wfile.write(b"{}")
s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
open(os.path.join(T, "port"), "w").write(str(s.server_address[1]))
s.serve_forever()
PY
T="$T" python3 "$T/server.py" & SERVER=$!; disown "$SERVER"
trap 'kill $SERVER 2>/dev/null; rm -rf "$T"' EXIT
for _ in $(seq 1 50); do [ -s "$T/port" ] && break; sleep 0.1; done
URL="http://127.0.0.1:$(cat "$T/port")"
sed -e "s#https://gitlab.example.com#$URL#" -e "s#https://github.com#$URL#" "$ROOT/routing.yaml" >"$T/cfg/routing.yaml"
export AGENT_OPS_CONFIG_ROOT="$T/cfg" AGENT_OPS_ENV=/dev/null CODEHOST_MAIN_RELEASE_TOKEN="gl-rel" CODEHOST_OSS_RELEASE_TOKEN="gh-rel"
SHA=0123456789abcdef
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
posts() { [ -f "$T/posts" ] && wc -l < "$T/posts" | tr -d ' ' || echo 0; }
post() { bash "$ROOT/infra/bin/post-commit-status.sh" "$P" "$SHA" "$1" "$2" >/dev/null 2>&1; }

# GitLab (code host "main")
P=acme/billing-backend
post success "E2E green"
check "GitLab: lookup fails → posts anyway" '[ "$(posts)" = 1 ] && grep -q "^/api/v4/projects/acme%2Fbilling-backend/statuses/$SHA gl-rel .*state=success" "$T/posts"'
echo '[{"id":1,"status":"pending","description":"E2E infra error, rerun needed"},{"id":2,"status":"success","description":"E2E green"}]' > "$T/statuses.json"
post success "E2E green"
check "GitLab: same newest status → not posted again" '[ "$(posts)" = 1 ]'
post failed "2 unattributed failures"
check "GitLab: different state → posted" '[ "$(posts)" = 2 ]'
post success "E2E green, data checks ok"
check "GitLab: different description → posted" '[ "$(posts)" = 3 ]'
echo '[{"id":5,"status":"success","description":"E2E green"},{"id":4,"status":"failed","description":"x"}]' > "$T/statuses.json"
post success "E2E green"
check "GitLab: newest is by id, not list order" '[ "$(posts)" = 3 ]'

# GitHub (code host "oss"): statuses API, failed → failure, context = release-agent
rm -f "$T/posts" "$T/statuses.json"; P=acme/warehouse
post failed "1 data check failed"
check "GitHub: posts to /repos/<owner>/<repo>/statuses/<sha> with the release token" \
  'grep -q "^/api/v3/repos/acme/warehouse/statuses/$SHA Bearer gh-rel " "$T/posts" && grep -q "\"state\": \"failure\"" "$T/posts" && grep -q "\"context\": \"release-agent\"" "$T/posts"'
echo '[{"id":9,"state":"failure","context":"release-agent","description":"1 data check failed"},{"id":8,"state":"success","context":"other-ci","description":"x"}]' > "$T/statuses.json"
post failed "1 data check failed"
check "GitHub: same newest release-agent status → not posted again" '[ "$(posts)" = 1 ]'
post success "all green"
check "GitHub: different state → posted" '[ "$(posts)" = 2 ]'

# refusals
P=acme/not-configured; post success x; rc=$?
check "repo not in routing.yaml → refused" '[ "$rc" = 1 ] && [ "$(posts)" = 2 ]'
P=acme/billing-backend; ( unset CODEHOST_MAIN_RELEASE_TOKEN; bash "$ROOT/infra/bin/post-commit-status.sh" "$P" "$SHA" success x 2>&1 ) | grep -q "CODEHOST_MAIN_RELEASE_TOKEN is not set" \
  && echo "ok   no token → refused, names the variable" || { echo "FAIL no token message"; fail=1; }
check "journal records each post" '[ -s "$AGENT_JOURNAL_DB" ]'
exit "$fail"
