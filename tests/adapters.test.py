#!/usr/bin/env python3
"""Offline tests for infra/adapters: the YAML reader, the factory (adapters and tokens per role),
Plane as the tracker, GitLab and GitHub as code hosts, each against a fake HTTP API, and the
`infra/bin/adapter` CLI. Run: python3 tests/adapters.test.py"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra"))
import adapters as A  # noqa: E402
from adapters import config as C  # noqa: E402

fails: list = []
CALLS: list = []


def check(cond, label, detail=""):
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  {str(detail)[:400]}"))
    if not cond:
        fails.append(label)


# ---- one fake server for all three APIs ----------------------------------------------------------
ITEMS = [{"id": "w1", "sequence_id": 12, "name": "Fix totals", "state": "s-in", "labels": ["l-q"], "assignees": [],
          "updated_at": "2026-10-20T10:00:00Z"},
         {"id": "w2", "sequence_id": 13, "name": "Report", "state": "s-done", "labels": [], "assignees": ["u1"],
          "updated_at": "2026-10-10T10:00:00Z"}]


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode() if n else ""
        if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
            return {k: v[0] for k, v in parse_qs(raw).items()}
        return json.loads(raw) if raw else None

    def handle_any(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = self._body() if method in ("POST", "PUT", "PATCH") else None
        auth = self.headers.get("X-API-Key") or self.headers.get("PRIVATE-TOKEN") or self.headers.get("Authorization")
        CALLS.append((method, u.path, q, body, auth))
        p = u.path
        # Plane
        if p == "/api/v1/users/me/":
            return self._send(200, {"id": "u-me", "email": "Alex@Example.com", "display_name": "alex"})
        if p == "/api/v1/workspaces/ws/projects/":
            if q.get("cursor") == "c2":
                return self._send(200, {"results": [{"id": "p2", "identifier": "PRC", "name": "Acme Pricing", "is_member": False}],
                                        "next_page_results": False})
            return self._send(200, {"results": [{"id": "p1", "identifier": "BIL", "name": "Acme Billing", "is_member": True}],
                                    "next_page_results": True, "next_cursor": "c2"})
        if p == "/api/v1/workspaces/ws/projects/p1/states/":
            return self._send(200, {"results": [{"id": "s-in", "name": "Inbox", "group": "backlog"},
                                                {"id": "s-done", "name": "Done", "group": "completed"}]})
        if p == "/api/v1/workspaces/ws/projects/p1/labels/":
            return self._send(200, {"results": [{"id": "l-q", "name": "question"}]})
        if p == "/api/v1/workspaces/ws/projects/p1/work-items/":
            return self._send(404, {"error": "old Plane"})          # → falls back to /issues/
        if p == "/api/v1/workspaces/ws/projects/p1/issues/":
            return self._send(200, {"results": ITEMS, "next_page_results": False})
        if p == "/api/v1/workspaces/ws/projects/p1/work-items/w1/" and method == "PATCH":
            return self._send(200, {**ITEMS[0], **(body or {})})
        if p == "/api/v1/workspaces/ws/projects/p1/work-items/w1/activities/":
            return self._send(200, {"results": [{"field": "state", "actor": {"id": "u-noor"}, "created_at": "2026-10-20T11:00:00Z"},
                                                {"field": "name", "actor_detail": {"id": "u-sam"}, "created_at": "2026-10-20T12:00:00Z"}]})
        if p.startswith("/api/v1/workspaces/ws/projects/p1/work-items/w1/comments/"):
            return self._send(204 if method == "DELETE" else 200, None if method == "DELETE" else {"id": "c1"})
        if p == "/api/v1/workspaces/ws/members/":
            return self._send(200, [{"member": {"id": "u-me", "email": "Alex@Example.com", "display_name": "alex"}}])
        if p == "/api/v1/workspaces/ws/work-items/BIL-12/":
            return self._send(200, {**ITEMS[0], "project": "p1"})
        # GitLab
        if p == "/gl/api/v4/projects/acme%2Fbilling-backend/merge_requests":
            return self._send(200, [{"iid": 4, "title": "x", "sha": "abc", "source_branch": "f", "target_branch": "main",
                                     "author": {"username": "noor"}, "updated_at": "2026-10-20T10:00:00Z", "labels": []}])
        if p == "/gl/api/v4/projects/acme%2Fbilling-backend/repository/commits/abc/statuses":
            return self._send(200, [{"id": 7, "status": "success", "name": "release-agent", "description": "b"},
                                    {"id": 3, "status": "failed", "name": "release-agent", "description": "a"}])
        if p == "/gl/api/v4/projects/acme%2Fbilling-backend/statuses/abc" and method == "POST":
            return self._send(201, {"id": 8})
        if p == "/gl/api/v4/projects/acme%2Fbilling-backend/merge_requests/4" and method == "PUT":
            return self._send(200, {"iid": 4, "state": "closed"})
        if p.startswith("/gl/api/v4/projects/acme%2Fbilling-backend/merge_requests/4/notes/"):
            return self._send(204, None)
        if p == "/gl/api/v4/user":
            return self._send(200, {"username": "agent-bot"})
        # GitHub (Enterprise layout: <url>/api/v3)
        if p == "/gh/api/v3/repos/acme/warehouse/pulls":
            return self._send(200, [
                {"number": 9, "title": "new", "state": "open", "head": {"ref": "f", "sha": "def"}, "base": {"ref": "main"},
                 "user": {"login": "kenji"}, "updated_at": "2026-10-20T10:00:00Z", "merged_at": None, "labels": [{"name": "data"}]},
                {"number": 8, "title": "old", "state": "closed", "head": {"ref": "g", "sha": "aaa"}, "base": {"ref": "main"},
                 "user": {"login": "kenji"}, "updated_at": "2026-10-01T10:00:00Z", "merged_at": "2026-10-01T10:00:00Z", "labels": []}])
        if p == "/gh/api/v3/repos/acme/warehouse/commits/def/statuses":
            return self._send(200, [{"id": 2, "state": "failure", "context": "release-agent", "description": "red"},
                                    {"id": 5, "state": "error", "context": "release-agent", "description": "reverted"},
                                    {"id": 4, "state": "success", "context": "ci", "description": "other"}])
        if p == "/gh/api/v3/repos/acme/warehouse/statuses/def" and method == "POST":
            return self._send(201, {"id": 6})
        if p == "/gh/api/v3/repos/acme/warehouse/pulls/9" and method == "PATCH":
            return self._send(200, {"number": 9, "state": "closed"})
        if p == "/gh/api/v3/repos/acme/warehouse/issues/9/labels" and method == "PUT":
            return self._send(200, [])
        if p == "/gh/api/v3/repos/acme/warehouse/issues/comments/55" and method == "DELETE":
            return self._send(204, None)
        if p == "/gh/api/v3/repos/acme/warehouse/pulls/9/files":
            return self._send(200, [{"filename": "models/orders.sql"}])
        return self._send(404, {"message": f"no route {method} {p}"})

    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")
    def do_PUT(self): self.handle_any("PUT")
    def do_PATCH(self): self.handle_any("PATCH")
    def do_DELETE(self): self.handle_any("DELETE")


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}"
CFG = {"tracker": "plane", "code_hosts": {"main": {"type": "gitlab", "url": f"{URL}/gl"},
                                          "oss": {"type": "github", "url": f"{URL}/gh"}}}
ENV = {"TRACKER_BASE_URL": URL, "TRACKER_WORKSPACE": "ws", "TRACKER_TRIAGE_TOKEN": "tri-tok",
       "TRACKER_RELEASE_TOKEN": "rel-tok", "CODEHOST_MAIN_TOKEN": "gl-read", "CODEHOST_MAIN_RELEASE_TOKEN": "gl-rel",
       "CODEHOST_OSS_TOKEN": "gh-read"}


def last(path_part):
    return next((c for c in reversed(CALLS) if path_part in c[1]), None)


# ---- YAML reader ---------------------------------------------------------------------------------
y = C.parse_yaml("""# comment
a: 1
b: {x: [1, 2], y: "q # not a comment"}   # trailing
c:
  - name: one
    path: /srv/x
  - name: two
d:
  e:
    - plain
    - 'quoted'
f: [a, b]
g: true
h:
""")
check(y == {"a": 1, "b": {"x": [1, 2], "y": "q # not a comment"}, "c": [{"name": "one", "path": "/srv/x"}, {"name": "two"}],
            "d": {"e": ["plain", "quoted"]}, "f": ["a", "b"], "g": True, "h": None}, "yaml: maps, lists of maps, inline, quotes, comments", y)
r = A.load_routing(ROOT)
check(r.get("tracker") == "plane" and set(r.get("code_hosts") or {}) == {"main", "oss"}, "yaml: the example routing.yaml reads without PyYAML")
repos = A.all_repos(r, release_only=True)
check([x["path"] for x in repos] == ["acme/billing-backend", "acme/billing-frontend", "acme/warehouse", "acme/pricing"]
      and all(x["path"] != "acme/pricing-admin" for x in repos), "all_repos: every team and project, de-duplicated, release: false skipped",
      [x["path"] for x in repos])

# ---- factory -------------------------------------------------------------------------------------
t = A.tracker(CFG, role="triage", env=ENV)
check(t.name == "plane" and t.token == "tri-tok", "factory: tracker from routing.yaml with the role's token")
check(A.tracker(CFG, role="release", env=ENV).token == "rel-tok", "factory: release role → TRACKER_RELEASE_TOKEN")
tmp = Path(tempfile.mkdtemp())
(tmp / "nightly.token").write_text("ngt-tok\n")
check(A.tracker(CFG, role="nightly", env={**ENV, "TRACKER_NIGHTLY_TOKEN_FILE": str(tmp / "nightly.token")}).token == "ngt-tok",
      "factory: nightly token from TRACKER_NIGHTLY_TOKEN_FILE")
(tmp / "revert.env").write_text("TRACKER_REVERT_TOKEN=rv-tok\nCODEHOST_MAIN_REVERT_TOKEN=gl-rv\n")
os.environ["AGENT_REVERT_ENV"] = str(tmp / "revert.env")
check(A.tracker(CFG, role="revert", env=ENV).token == "rv-tok" and A.codehost(CFG, "main", kind="revert", env=ENV).token == "gl-rv",
      "factory: revert tokens only from revert.env")
check(A.tracker(CFG, role="revert", env={**ENV, "TRACKER_REVERT_TOKEN": "from-shared-env"}).token == "rv-tok",
      "factory: a revert token in the shared env is ignored")
check(A.codehost(CFG, "main", kind="release", env=ENV).token == "gl-rel" and A.codehost(CFG, "oss", kind="release", env=ENV).token == "gh-read",
      "factory: release token, falling back to the read token")
check(A.codehost_for_url(f"{URL}/gh/acme/warehouse.git".replace("/gh/", "/gh/"), CFG, env=ENV) is not None, "factory: code host by URL")
check(A.codehost_for_url("https://elsewhere.example.com/x.git", CFG, env=ENV) is None, "factory: unknown host → None")
try:
    A.codehost(CFG, "nope", env=ENV)
    check(False, "factory: unknown code host key raises")
except ValueError:
    check(True, "factory: unknown code host key raises")

# ---- Plane ---------------------------------------------------------------------------------------
check(t.whoami() == {"id": "u-me", "email": "alex@example.com", "display_name": "alex"}, "plane: whoami, email lower-cased")
ps = t.projects()
check([p["identifier"] for p in ps] == ["BIL", "PRC"] and ps[1]["is_member"] is False, "plane: projects across cursor pages", ps)
items = t.list_items("p1")
check(items[0]["key"] == "BIL-12" and items[0]["state_name"] == "Inbox" and items[0]["labels"] == ["question"]
      and items[0]["label_ids"] == ["l-q"] and items[0]["state_group"] == "backlog", "plane: items normalised (key, state, labels)", items[0])
check(any("/issues/" in c[1] for c in CALLS) and last("/issues/")[4] == "tri-tok", "plane: /work-items/ 404 → /issues/ fallback, token in X-API-Key")
check([i["key"] for i in t.list_items("p1", state_names=["Done"])] == ["BIL-13"], "plane: filter by state name")
check([i["key"] for i in t.list_items("p1", updated_since="2026-10-15T00:00:00Z")] == ["BIL-12"], "plane: filter by updated_since")
check([a["actor"] for a in t.activities("p1", "w1")] == ["u-noor", "u-sam"], "plane: activity actors normalised")
upd = t.update_item("p1", "w1", {"state": "s-done"})
check(upd["state_name"] == "Done" and last("/work-items/w1/")[3] == {"state": "s-done"}, "plane: update_item PATCHes the fields")
t.delete_comment("p1", "w1", "c9")
check(last("/comments/c9/")[0] == "DELETE", "plane: delete_comment")
check(t.members() == [{"id": "u-me", "email": "alex@example.com", "display_name": "alex"}], "plane: workspace members")
check(t.get_item_by_key("bil-12")["key"] == "BIL-12", "plane: get_item_by_key")

# ---- GitLab --------------------------------------------------------------------------------------
gl = A.codehost(CFG, "main", kind="release", env=ENV)
mrs = gl.list_merge_requests("acme/billing-backend", state="all", target_branch="main", updated_after="2026-10-19T00:00:00Z")
c = last("merge_requests")
check(mrs[0]["iid"] == 4 and mrs[0]["author"] == "noor" and c[2].get("target_branch") == "main" and c[2].get("updated_after")
      and c[4] == "gl-rel", "gitlab: MRs into main since a time, PRIVATE-TOKEN header", (mrs, c))
st = gl.statuses("acme/billing-backend", "abc", name="release-agent")
check([s["id"] for s in st] == [3, 7] and st[-1]["status"] == "success", "gitlab: statuses oldest first (by id)")
gl.post_status("acme/billing-backend", "abc", "failed", "release-agent", "x" * 300, "https://ci.example.com/1")
c = last("/statuses/abc")
check(c[3] == {"state": "failed", "name": "release-agent", "description": "x" * 250, "target_url": "https://ci.example.com/1"},
      "gitlab: post_status (form fields, description cut to 250)", c)
gl.close_merge_request("acme/billing-backend", 4)
check(last("merge_requests/4")[3] == {"state_event": "close"}, "gitlab: close MR")
gl.delete_note("acme/billing-backend", "merge_request", 4, "77")
check(last("/notes/77")[0] == "DELETE", "gitlab: delete MR note")
check(gl.clone_url("acme/billing-backend") == f"{URL}/gl/acme/billing-backend.git", "gitlab: clone URL without a token")
check(gl.git_auth_header().startswith("Authorization: Basic ") and "gl-rel" not in gl.git_auth_header(), "gitlab: git auth header (base64)")

# ---- GitHub --------------------------------------------------------------------------------------
gh = A.codehost(CFG, "oss", env=ENV)
check(gh.api == f"{URL}/gh/api/v3" and A.codehost({"code_hosts": {"x": {"type": "github", "url": "https://github.com"}}}, "x", env={}).api
      == "https://api.github.com", "github: API base for Enterprise and github.com")
prs = gh.list_merge_requests("acme/warehouse", state="all", target_branch="main", updated_after="2026-10-19T00:00:00Z")
c = last("/pulls")
check([p["iid"] for p in prs] == [9] and prs[0]["sha"] == "def" and prs[0]["labels"] == ["data"] and c[2].get("base") == "main"
      and c[4] == "Bearer gh-read", "github: PRs into main, updated_after filtered client-side, Bearer token", (prs, c))
merged = gh.list_merge_requests("acme/warehouse", state="merged")
check([p["iid"] for p in merged] == [8] and merged[0]["state"] == "merged", "github: merged = closed with merged_at")
st = gh.statuses("acme/warehouse", "def", name="release-agent")
check([s["status"] for s in st] == ["failed", "canceled"], "github: statuses mapped back (failure→failed, error→canceled), other contexts dropped", st)
gh.post_status("acme/warehouse", "def", "canceled", "release-agent", "y" * 200)
c = last("/statuses/def")
check(c[3] == {"state": "error", "context": "release-agent", "description": "y" * 140}, "github: post_status maps canceled→error, context, 140 chars", c)
gh.update_merge_request("acme/warehouse", 9, {"labels": "a,b"})
check(last("/issues/9/labels")[3] == {"labels": ["a", "b"]}, "github: labels through the issues API")
gh.close_merge_request("acme/warehouse", 9)
check(last("/pulls/9")[3] == {"state": "closed"}, "github: close PR")
gh.delete_note("acme/warehouse", "merge_request", 9, "55")
check(last("/issues/comments/55")[0] == "DELETE", "github: delete PR comment (issue comment)")
check(gh.merge_request_files("acme/warehouse", 9) == ["models/orders.sql"], "github: PR files")
check(A.codehost(CFG, "oss", token="t", env={}).git_auth_header() == "Authorization: Basic eC1hY2Nlc3MtdG9rZW46dA==",
      "github: git auth header uses x-access-token")

# ---- errors never carry the token -----------------------------------------------------------------
try:
    A.codehost(CFG, "main", token="secret-token-123", env={}).get_merge_request("acme/unknown", 1)
    check(False, "errors: a 404 raises AdapterError")
except A.AdapterError as e:
    check(e.status == 404 and "secret-token-123" not in str(e), "errors: AdapterError with status, token redacted")

# ---- CLI -----------------------------------------------------------------------------------------
cfgdir = Path(tempfile.mkdtemp())
(cfgdir / "routing.yaml").write_text(f"tracker: plane\ncode_hosts:\n  main: {{type: gitlab, url: {URL}/gl}}\n"
                                     "teams:\n  backend:\n    person: alex\n    repos:\n      - host: main\n        path: acme/billing-backend\n")
env = {**os.environ, "AGENT_OPS_CONFIG_ROOT": str(cfgdir), "AGENT_OPS_ENV": "/dev/null", **ENV}
cli = [sys.executable, "-I", str(ROOT / "infra/bin/adapter")]
out = subprocess.run(cli + ["codehost", "main", "list_merge_requests", '["acme/billing-backend"]'], capture_output=True, text=True, env=env)
check(out.returncode == 0 and json.loads(out.stdout)[0]["iid"] == 4, "cli: codehost method with JSON args", out.stderr)
out = subprocess.run(cli + ["tracker", "list_items", '{"project_id": "p1", "state_names": ["Inbox"]}'], capture_output=True, text=True, env=env)
check(out.returncode == 0 and [i["key"] for i in json.loads(out.stdout)] == ["BIL-12"], "cli: tracker method with keyword args", out.stderr)
out = subprocess.run(cli + ["repos"], capture_output=True, text=True, env=env)
check(json.loads(out.stdout) == [{"host": "main", "path": "acme/billing-backend"}], "cli: repos")
out = subprocess.run(cli + ["git-auth", f"{URL}/gl/acme/billing-backend.git"], capture_output=True, text=True, env=env)
check(out.stdout.startswith("Authorization: Basic ") and "gl-read" not in out.stdout, "cli: git-auth header for a configured host")
out = subprocess.run(cli + ["git-auth", "https://elsewhere.example.com/x.git"], capture_output=True, text=True, env=env)
check(out.returncode == 0 and out.stdout == "", "cli: git-auth prints nothing for an unknown host")
out = subprocess.run(cli + ["tracker", "_call", "[]"], capture_output=True, text=True, env=env)
check(out.returncode == 2, "cli: private methods are refused (exit 2)")
out = subprocess.run(cli + ["codehost", "main", "get_merge_request", '["acme/unknown", 1]'], capture_output=True, text=True, env=env)
check(out.returncode == 1 and "gl-read" not in out.stderr, "cli: API error → exit 1, token redacted")

print(f"\n{'all passed' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
