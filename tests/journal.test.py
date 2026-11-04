#!/usr/bin/env python3
"""Offline tests for the action journal (infra/journal/journal.py) and its hook
(plugins/core/scripts/journal.sh): fake tracker and code-host adapters, a fake /journal sink, a throwaway
git repo. Run: python3 tests/journal.test.py"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra" / "journal"))
import journal as J  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="journal-test-"))
PID = "00000000-0000-4000-8000-000000000001"
PROJECTS = {"BIL": {"key": "BIL", "tracker_project_id": PID, "repos": []}}
HOSTS = {"main": {"type": "gitlab", "url": "https://gitlab.example.com"}, "oss": {"type": "github", "url": "https://github.com"}}
fails: list = []


def check(cond, label, detail=""):
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  {str(detail)[:400]}"))
    if not cond:
        fails.append(label)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


# ---- fake adapters (the interface in infra/adapters/README.md) ---------------------------------
ITEMS = {"w1": {"id": "w1", "key": "BIL-7", "name": "Fix it", "state": "s-todo", "priority": "medium",
                "label_ids": ["l-a"], "assignees": [], "description_html": "<p>x</p>"}}
STATES = [{"id": "s-todo", "group": "unstarted"}, {"id": "s-prog", "group": "started"}, {"id": "s-cxl", "group": "cancelled"}]
CALLS: list = []          # (method, args, token)
ACTIVITY: list = []
GOOD = {"tok-alex", "tok-revert"}


class FakeTracker:
    def __init__(self, token):
        self.token = token

    def _log(self, m, *a):
        if self.token not in GOOD:
            raise RuntimeError("HTTP 403: bad key")
        CALLS.append((m, a, self.token))

    def get_item(self, pid, wid):
        self._log("get_item", pid, wid)
        return dict(ITEMS[wid])

    def get_item_by_key(self, key):
        self._log("get_item_by_key", key)
        return next((dict(i) for i in ITEMS.values() if i.get("key") == key), None)

    def update_item(self, pid, wid, fields):
        self._log("update_item", pid, wid, fields)
        ITEMS[wid].update(fields)
        return dict(ITEMS[wid])

    def states(self, pid):
        self._log("states", pid)
        return STATES

    def activities(self, pid, wid):
        self._log("activities", pid, wid)
        return ACTIVITY

    def list_comments(self, pid, wid):
        self._log("list_comments", pid, wid)
        return [{"id": "c-9", "comment_html": "<p>old text</p>"}]

    def add_comment(self, pid, wid, html):
        self._log("add_comment", pid, wid, html)
        return {"id": "c-new"}

    def update_comment(self, pid, wid, cid, html):
        self._log("update_comment", pid, wid, cid, html)

    def delete_comment(self, pid, wid, cid):
        self._log("delete_comment", pid, wid, cid)

    def delete_link(self, pid, wid, lid):
        self._log("delete_link", pid, wid, lid)


CH_CALLS: list = []       # (key, method, args, token)


class FakeCodeHost:
    def __init__(self, key, token):
        self.key, self.token = key, token

    def _log(self, m, *a):
        CH_CALLS.append((self.key, m, a, self.token))

    def get_merge_request(self, repo, iid):
        self._log("get_merge_request", repo, iid)
        return {"iid": iid, "title": "old title", "description": "old", "labels": ["a"], "state": "opened", "target_branch": "dev"}

    def update_merge_request(self, repo, iid, fields):
        self._log("update_merge_request", repo, iid, fields)

    def close_merge_request(self, repo, iid):
        self._log("close_merge_request", repo, iid)

    def close_issue(self, repo, iid):
        self._log("close_issue", repo, iid)

    def delete_note(self, repo, kind, iid, note_id):
        self._log("delete_note", repo, kind, iid, note_id)

    def post_status(self, repo, sha, state, name, description, target_url=None):
        self._log("post_status", repo, sha, state, name)


CONNECTOR = J.Connector({}, tracker_factory=FakeTracker, codehost_factory=FakeCodeHost, hosts=HOSTS)


SINK: list = []
SINK_UP = {"on": True}


class Sink(BaseHTTPRequestHandler):
    """Stands in for agent-mcp's /journal: records what the hook sends."""
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if not SINK_UP["on"]:
            self.send_response(503); self.end_headers(); return
        SINK.append({"path": self.path, "phase": self.headers.get("X-Journal-Phase"),
                     "auth": self.headers.get("Authorization"), "name": self.headers.get("X-Agent-Name"),
                     "run": self.headers.get("X-Agent-Run"), "role": self.headers.get("X-Agent-Role"), "body": body})
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b'{"ok":true}')


def serve(handler):
    port = free_port()
    s = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return port


# ---- tests ---------------------------------------------------------------------------------------
def test_classify():
    c = J.classify
    check(c("mcp__plane__workitem", {"action": "list"}) is None and c("mcp__plugin_core_plane__workitem", {"action": "retrieve_by_identifier"}) is None,
          "task-board reads are not journaled")
    w = c("mcp__plugin_core_plane__workitem", {"action": "update", "project_id": PID, "workitem_id": "w1", "state": "x"})
    check(w and w["system"] == "tracker" and w["reversible"] == "auto" and w["snapshot"] == "item",
          "task-board update: auto revert with a snapshot", w)
    check(c("mcp__plugin_role-backend_gitlab__get_merge_request", {}) is None and c("mcp__github__get_pull_request", {}) is None,
          "code-host reads are not journaled")
    m = c("mcp__plugin_role-backend_gitlab__create_merge_request", {"project_id": "acme/billing-backend"})
    check(m and m["reversible"] == "auto" and m["host_type"] == "gitlab", "GitLab create MR: auto revert (close)", m)
    gh = c("mcp__github__create_pull_request", {"owner": "acme", "repo": "warehouse", "title": "x"})
    check(gh and gh["kind"] == "mr_create" and gh["host_type"] == "github" and gh["project_id"] == "acme/warehouse",
          "GitHub create PR: same as an MR, owner/repo as the project", gh)
    up = c("mcp__github__update_pull_request", {"owner": "acme", "repo": "warehouse", "pullNumber": 4, "title": "y"})
    check(up and up["snapshot"] == "merge_request" and up["target_id"] == "acme/warehouse!4", "GitHub update PR: snapshot first", up)
    note = c("mcp__github__add_issue_comment", {"owner": "acme", "repo": "warehouse", "issue_number": 5, "body": "hi"})
    check(note and note["kind"] == "note_create" and note["note_on"] == "issue", "GitHub comment: deletable note", note)
    check(J.host_key("github", "github", HOSTS) == "oss" and J.host_key("gitlab-main", "gitlab", HOSTS) == "main"
          and J.host_key("plugin_role-backend_gitlab", "gitlab", HOSTS) == "main", "code host key from the MCP server name")
    check(c("Bash", {"command": "ls -la && git status"}) is None, "harmless shell commands are not journaled")
    g = c("Bash", {"command": "git push -u origin feat/x"})
    check(g and g["reversible"] == "manual" and "git revert" in g["hint"], "git push: manual undo with a hint", g)
    t = c("mcp__plugin_core_agent-mcp__send_message", {"to": ["Noor"], "text": "secret plans"})
    check(t and t["reversible"] == "no", "Telegram message: can't be undone", t)
    check(c("mcp__plugin_role-backend_jenkins__triggerBuild", {"jobFullName": "x"})["system"] == "jenkins"
          and c("mcp__plugin_role-backend_jenkins__getBuild", {}) is None, "jenkins: writes only")
    check(c("Artifact", {"action": "read", "url": "u"}) is None and c("Artifact", {"file_path": "p.html"})["system"] == "artifact",
          "artifact: publish journaled, read not")


def test_journal():
    j = J.Journal(TMP / "j.sqlite3", PROJECTS, HOSTS)
    tracker = FakeTracker("tok-alex")
    alex = {"kind": "person-agent", "person": "alex", "user_id": "u-alex", "email": "alex@example.com", "role": "backend",
            "client": "claude-code"}
    ti = {"action": "update", "project_id": PID, "workitem_id": "w1", "state": "s-prog"}
    j.ingest("pre", {"tool_name": "mcp__plugin_core_plane__workitem", "tool_input": ti, "tool_use_id": "t1"}, alex, tracker)
    ITEMS["w1"]["state"] = "s-prog"          # the write itself (the agent's task-board call)
    eid = j.ingest("post", {"tool_name": "mcp__plugin_core_plane__workitem", "tool_input": ti, "tool_use_id": "t1",
                            "session_id": "sess-1", "transcript_path": "/x.jsonl",
                            "tool_response": {"content": [{"type": "text", "text": json.dumps(ITEMS["w1"])}]}}, alex, tracker)
    e = j.events(event=eid)[0]
    check(e["target_key"] == "BIL-7" and json.loads(e["before"])["state"] == "s-todo" and json.loads(e["after"])["state"] == "s-prog",
          "update: before and after saved, key BIL-7", e)
    check("(state)" in e["summary"] and e["person"] == "alex", "summary names the changed field", e["summary"])
    check(not j.q("select * from pending"), "the pre snapshot is consumed by its post")

    # label added by the triage bot
    bot = {"kind": "service-agent", "agent": "triage", "run_id": "triage-1", "user_id": "u-bot", "client": "service"}
    ti2 = {"action": "manage_label", "project_id": PID, "workitem_id": "w1", "add_label_id": "l-b"}
    j.ingest("pre", {"tool_name": "mcp__plane__workitem", "tool_input": ti2, "tool_use_id": "t2"}, bot, tracker)
    ITEMS["w1"]["label_ids"] = ["l-a", "l-b"]
    j.ingest("post", {"tool_name": "mcp__plane__workitem", "tool_input": ti2, "tool_use_id": "t2", "tool_response": "{}"}, bot, tracker)

    # comment by the triage bot (an MCP server's usual shape: a string with {"result": {...}})
    j.ingest("post", {"tool_name": "mcp__plane__workitem_comment", "tool_use_id": "t3",
                      "tool_input": {"action": "create", "project_id": PID, "workitem_id": "w1", "comment_html": "<p>[agent:triage] hi</p>"},
                      "tool_response": '{"result": {"id": "c-1", "comment_html": "<p>hi</p>"}}'}, bot, tracker)

    # blame: three journal rows + one hand change from the item's activity
    ACTIVITY.append({"actor": {"id": "u-sam", "display_name": "Sam"}, "field": "priority",
                     "old_value": "medium", "new_value": "high", "created_at": "2026-11-04T10:00:00Z"})
    rows = j.blame("BIL-7", tracker=tracker)
    hows = " | ".join(r["how"] for r in rows)
    check(len(rows) == 4 and "service agent run triage-1" in hows and "alex's agent" in hows and "by hand on the task board" in hows,
          "blame: person's agent, service-agent run and by-hand changes", rows)

    # revert the triage run: label removed again, comment deleted; Alex's state change untouched
    plan = j.plan({"run": "triage-1"}, created_by="alex")
    ops = sorted(s["op"] for s in plan["steps"])
    check(ops == ["tracker.delete_comment", "tracker.restore"], "plan for a run: only that run's changes", plan)
    revert_tracker = FakeTracker("tok-revert")
    res = j.apply(plan["plan"], "alex", revert_tracker, {})
    check(ITEMS["w1"]["label_ids"] == ["l-a"] and ITEMS["w1"]["state"] == "s-prog", "apply: label removed, other changes kept", ITEMS["w1"])
    check(any(c[0] == "delete_comment" and c[1][2] == "c-1" and c[2] == "tok-revert" for c in CALLS),
          "apply: the bot's comment deleted with the revert token", CALLS[-4:])
    check(all(r["ok"] for r in res["results"]), "apply: every step ok", res)
    try:
        j.apply(plan["plan"], "alex", revert_tracker, {})
        check(False, "a plan can't be applied twice")
    except ValueError:
        check(True, "a plan can't be applied twice")
    again = j.plan({"run": "triage-1"})
    check(again["steps"] == [], "reverted events are not planned again", again)
    check(any(e["actor_kind"] == "revert" and e["revert_of"] for e in j.events(limit=50)), "the revert itself is journaled")

    # tokens per step: a service agent's own change is undone with its own bot token, a person's
    # agent's change with the person's revert token (revert.env), never one for the other
    ITEMS["w1"]["label_ids"] = ["l-a"]
    bot2 = {**bot, "run_id": "triage-2"}
    j.ingest("pre", {"tool_name": "mcp__plane__workitem", "tool_input": {**ti2, "add_label_id": "l-c"}, "tool_use_id": "t8"}, bot2, tracker)
    ITEMS["w1"]["label_ids"] = ["l-a", "l-c"]
    j.ingest("post", {"tool_name": "mcp__plane__workitem", "tool_input": {**ti2, "add_label_id": "l-c"}, "tool_use_id": "t8",
                      "tool_response": "{}"}, bot2, tracker)
    rc = J.RevertClients({"TRACKER_TRIAGE_TOKEN": "tok-alex"}, {"TRACKER_REVERT_TOKEN": "tok-revert"}, CONNECTOR)
    n0 = len(CALLS)
    res = j.apply(j.plan({"run": "triage-2"})["plan"], "alex", clients=rc)
    check(ITEMS["w1"]["label_ids"] == ["l-a"] and all(c[2] == "tok-alex" for c in CALLS[n0:]) and "triage bot token" in res["results"][0]["result"],
          "tokens: a triage change is undone with the triage bot's own token", (CALLS[n0:], res))
    _, _, who = rc({"op": "tracker.restore", "actor_kind": "person-agent", "agent": None})
    check(who == "revert token", "tokens: a person's agent's change needs the person's revert token")
    t_none, _, _ = J.RevertClients({}, {}, CONNECTOR)({"op": "tracker.restore", "actor_kind": "person-agent"})
    check(t_none is None, "tokens: no revert.env → no token (the step fails and says so), never a bot token instead")
    _, ch, who = J.RevertClients({"CODEHOST_MAIN_RELEASE_TOKEN": "rel"}, {}, CONNECTOR)(
        {"op": "codehost.status", "actor_kind": "script", "agent": "release", "host": "main"})
    check(ch.token == "rel" and ch.key == "main" and who == "release bot token", "tokens: release's commit status is set back with the release token")
    _, ch, who = J.RevertClients({}, {"CODEHOST_OSS_REVERT_TOKEN": "gh-rev"}, CONNECTOR)(
        {"op": "codehost.close", "actor_kind": "person-agent", "host": "oss"})
    check(ch.token == "gh-rev" and who == "revert token", "tokens: a person's PR on the GitHub host uses that host's revert token")

    # conflict: Alex's state change, then someone moved it again → left alone
    ITEMS["w1"]["state"] = "s-cxl"
    p2 = j.plan({"event": eid})
    r2 = j.apply(p2["plan"], "alex", revert_tracker, {})
    check(ITEMS["w1"]["state"] == "s-cxl" and "changed again since" in r2["results"][0]["result"],
          "apply: a field changed again since is left alone and reported", r2)

    # create → cancelled
    ITEMS["w2"] = {"id": "w2", "key": "BIL-8", "name": "dup", "state": "s-todo", "label_ids": [], "assignees": []}
    j.ingest("post", {"tool_name": "mcp__plane__workitem", "tool_use_id": "t4",
                      "tool_input": {"action": "create", "project_id": PID, "name": "dup"},
                      "tool_response": {"content": [{"type": "text", "text": '{"id": "w2"}'}]}}, bot, tracker)
    p3 = j.plan({"target": "BIL-8"})
    j.apply(p3["plan"], "alex", revert_tracker, {})
    check(ITEMS["w2"]["state"] == "s-cxl", "apply: a created item is moved to cancelled (never deleted)", ITEMS["w2"])

    # GitLab MR created by a person's agent → closed; GitHub PR edit → restored from the snapshot
    j.ingest("post", {"tool_name": "mcp__plugin_role-backend_gitlab__create_merge_request", "tool_use_id": "t5",
                      "tool_input": {"project_id": "acme/billing-backend", "title": "x"},
                      "tool_response": {"content": [{"type": "text", "text": '{"id": 99, "iid": 12}'}]}}, alex)
    p4 = j.plan({"target": "acme/billing-backend!12"})
    j.apply(p4["plan"], "alex", None, {"main": FakeCodeHost("main", "gl-revert")})
    check(any(c[1] == "close_merge_request" and c[2] == ("acme/billing-backend", "12") and c[0] == "main" for c in CH_CALLS),
          "apply: an agent's MR is closed", CH_CALLS)
    gh_read = {"oss": FakeCodeHost("oss", "gh-read")}
    up = {"tool_name": "mcp__github__update_pull_request", "tool_use_id": "t9",
          "tool_input": {"owner": "acme", "repo": "warehouse", "pullNumber": 4, "title": "new title"}}
    j.ingest("pre", up, alex, None, gh_read)
    j.ingest("post", {**up, "tool_response": '{"number": 4}'}, alex, None, gh_read)
    p6 = j.plan({"target": "acme/warehouse!4"})
    j.apply(p6["plan"], "alex", None, {"oss": FakeCodeHost("oss", "gh-revert")})
    check(any(c[1] == "update_merge_request" and c[2][2].get("title") == "old title" and c[3] == "gh-revert" for c in CH_CALLS),
          "apply: a GitHub PR edit is restored from its snapshot", CH_CALLS[-2:])

    # Telegram: who/whom kept, never the text; can't be undone
    j.ingest("post", {"tool_name": "mcp__plugin_core_agent-mcp__send_message", "tool_use_id": "t6",
                      "tool_input": {"to": ["Noor"], "text": "secret plans"}, "tool_response": "sent"}, alex)
    row = j.events(text="telegram", limit=1)
    check(row and "secret plans" not in (row[0]["input"] or "") and row[0]["reversible"] == "no",
          "Telegram message: text not stored, marked irreversible", row)
    p5 = j.plan({"event": row[0]["id"]})
    check(p5["steps"][0]["op"] == "manual" and "unsent" in p5["steps"][0]["hint"], "irreversible steps are listed as manual", p5)

    # a failed write is recorded but never planned
    j.ingest("post", {"tool_name": "mcp__plane__workitem", "tool_use_id": "t7",
                      "tool_input": {"action": "update", "project_id": PID, "workitem_id": "w1", "state": "x"},
                      "tool_response": {"is_error": True, "content": "boom"}}, bot, tracker)
    check(j.events(limit=1)[0]["ok"] == 0 and not j.plan({"actor": "triage", "since": time.time() - 5})["steps"],
          "failed writes are recorded, not reverted")


def test_cli():
    db = TMP / "cli.sqlite3"
    env = {**os.environ, "AGENT_JOURNAL_DB": str(db), "AGENT_OPS_ENV": str(TMP / "none.env")}
    r = subprocess.run([sys.executable, "-I", str(ROOT / "infra/journal/journal.py"), "--db", str(db), "record", "--agent", "release",
                        "--system", "codehost", "--action", "commit_status", "--host", "main", "--project-id", "acme/billing-backend",
                        "--target-id", "abc123", "--target-key", "acme/billing-backend@abc123", "--reversible", "auto",
                        "--before", '{"sha":"abc123","name":"release-agent","state":"pending"}', "--summary", "status"],
                       capture_output=True, text=True, env=env)
    check(r.returncode == 0 and len(r.stdout.strip()) == 12, "record CLI writes an event", r.stderr)
    r = subprocess.run([sys.executable, "-I", str(ROOT / "infra/journal/journal.py"), "--db", str(db), "revert-plan", "--target", "acme/billing-backend@abc123"],
                       capture_output=True, text=True, env=env)
    plan = json.loads(r.stdout or "{}")
    check(plan.get("steps") and plan["steps"][0]["op"] == "codehost.status" and plan["steps"][0]["host"] == "main" and plan["steps"][0]["state"] == "pending",
          "commit status plan sets the previous state back", r.stdout + r.stderr)
    r = subprocess.run([sys.executable, "-I", str(ROOT / "infra/journal/journal.py"), "--db", str(db), "revert-apply", plan.get("plan", "x")],
                       capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    check(r.returncode != 0 and "terminal" in r.stderr, "revert-apply refuses to run without a terminal (agents)", r.stderr)
    # post-commit-status.py passes the code-host type as --system
    r = subprocess.run([sys.executable, "-I", str(ROOT / "infra/journal/journal.py"), "--db", str(db), "record", "--agent", "post-commit-status",
                        "--system", "github", "--action", "commit_status", "--host", "oss", "--project-id", "acme/pricing",
                        "--target-type", "commit", "--target-id", "def456", "--target-key", "acme/pricing@def456", "--reversible", "auto",
                        "--before", '{"sha":"def456","name":"release-agent","state":"canceled"}', "--summary", "status"],
                       capture_output=True, text=True, env=env)
    r = subprocess.run([sys.executable, "-I", str(ROOT / "infra/journal/journal.py"), "--db", str(db), "revert-plan", "--target", "acme/pricing@def456"],
                       capture_output=True, text=True, env=env)
    plan = json.loads(r.stdout or "{}")
    check(plan.get("steps") and plan["steps"][0]["op"] == "codehost.status" and plan["steps"][0]["host"] == "oss",
          "record --system github: a code-host status step on that host", r.stdout + r.stderr)


def test_suspects():
    repo = TMP / "repo"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True,
                                  env={**os.environ, "GIT_AUTHOR_NAME": "Noor", "GIT_AUTHOR_EMAIL": "noor@example.com",
                                       "GIT_COMMITTER_NAME": "Noor", "GIT_COMMITTER_EMAIL": "noor@example.com"})
    g("init", "-q", "-b", "main")
    (repo / "a.sql").write_text("select 1 from analytics.other\n"); g("add", "."); g("commit", "-qm", "unrelated")
    (repo / "b.sql").write_text("select * from analytics.payments_ledger\n"); g("add", "."); g("commit", "-qm", "use payments ledger",
                                                                                  "-m", "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    (repo / "payments_ledger_view.sql").write_text("x\n"); g("add", "."); g("commit", "-qm", "new view file")
    rows = J.suspects("payments_ledger", [{"name": "repo", "path": str(repo)}], 7)
    whats = [r["what"] for r in rows]
    check("use payments ledger" in whats and "new view file" in whats and "unrelated" not in whats,
          "suspects: commits that change the text or a matching path, not others", rows)
    up = J.suspects("revenue-dashboard-page", [{"name": "repo", "path": str(repo)}], 7, upstream=["payments_ledger"])
    check([r["what"] for r in up][:1] and any("(upstream)" in w for r in up for w in r["why"]) and "unrelated" not in [r["what"] for r in up],
          "suspects: changes to what a broken thing is built from (lineage upstream) count too", up)
    top = next(r for r in rows if r["what"] == "use payments ledger")
    check(top["agent_assisted"] and top["on_default_branch"] and top["author"] == "Noor",
          "suspects: author, agent-assisted and on the default branch", top)


def test_hook(sink_port):
    hook = ROOT / "plugins/core/scripts/journal.sh"
    data = TMP / "plugin-data"
    env = {**os.environ, "AGENT_JOURNAL_URL": f"http://127.0.0.1:{sink_port}/journal", "AGENT_JOURNAL_TOKEN": "tok-bot-xyz",
           "AGENT_NAME": "triage", "AGENT_RUN_ID": "triage-9", "CLAUDE_PLUGIN_DATA": str(data)}

    def run(phase, payload, wait=True):
        r = subprocess.run(["bash", str(hook), phase], input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=20)
        if wait and phase == "post":
            time.sleep(1.5)   # post sends in the background
        return r

    n0 = len(SINK)
    r = run("pre", {"tool_name": "mcp__plane__workitem", "tool_input": {"action": "list", "project_id": PID}})
    run("post", {"tool_name": "Bash", "tool_input": {"command": "git status && ls"}})
    check(len(SINK) == n0 and r.stdout == "" and r.returncode == 0, "hook: reads are not sent, nothing printed", SINK[n0:])
    w = {"tool_name": "mcp__plane__workitem", "tool_input": {"action": "update", "project_id": PID, "workitem_id": "w1"}, "tool_use_id": "u1"}
    r = run("pre", w)
    check(len(SINK) == n0 + 1 and SINK[-1]["phase"] == "pre" and SINK[-1]["auth"] == "Bearer tok-bot-xyz"
          and SINK[-1]["name"] == "triage" and SINK[-1]["run"] == "triage-9" and r.stdout == "",
          "hook pre: write sent at once with the agent's identity", SINK[-1:])
    run("post", {**w, "tool_response": "ok"})
    check(SINK[-1]["phase"] == "post" and json.loads(SINK[-1]["body"])["tool_use_id"] == "u1", "hook post: sent in the background", SINK[-1:])
    SINK_UP["on"] = False
    r = run("post", {**w, "tool_use_id": "u2", "tool_response": "ok"})
    spool = data / "journal-spool.jsonl"
    check(r.returncode == 0 and spool.is_file() and json.loads(spool.read_text().splitlines()[0])["payload"]["tool_use_id"] == "u2",
          "hook: agent-mcp down → the record waits in a spool file, the call isn't blocked", spool.read_text() if spool.exists() else "")
    SINK_UP["on"] = True
    run("post", {**w, "tool_use_id": "u3", "tool_response": "ok"})
    batch = [s for s in SINK if s["path"].endswith("/journal/batch")]
    check(batch and b'"u2"' in batch[-1]["body"] and not spool.exists(), "hook: the spool is sent with the next record", SINK[-2:])
    check(all("tok-bot-xyz" not in s["body"].decode() for s in SINK), "hook: the token is never in the body")
    env.pop("AGENT_JOURNAL_TOKEN")
    n1 = len(SINK)
    run("pre", w)
    check(len(SINK) == n1, "hook: no token → nothing sent, no error")


def main():
    sink_port = serve(Sink)
    test_classify()
    test_journal()
    test_cli()
    test_suspects()
    test_hook(sink_port)
    print(f"\n{'all passed' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
