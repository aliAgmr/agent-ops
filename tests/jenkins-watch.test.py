#!/usr/bin/env python3
"""Offline tests for the Jenkins watcher (infra/jenkins-watch/watch.py, also as the precheck) and the
Telegram poller (infra/telegram/poll.py): a fake Jenkins MCP server (JSON-RPC over HTTP) and a fake
Telegram Bot API, both on 127.0.0.1. Run: python3 tests/jenkins-watch.test.py"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socketserver

ROOT = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="jenkins-watch-test-"))
fails: list = []


def check(cond, label, detail=""):
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  {str(detail)[:500]}"))
    if not cond:
        fails.append(label)


class Server(ThreadingHTTPServer):
    # Skip HTTPServer's reverse-DNS lookup of 127.0.0.1 (socket.getfqdn): it takes ~35 s on GitHub's
    # macOS runners.
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def serve(handler):
    s = Server(("127.0.0.1", 0), handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_address[1]


NOW_MS = int(time.time() * 1000)
JOBS = {}      # name -> {"color", "lastBuild": {...}}
BUILDS = {}    # (name, number or None) -> build dict
QUEUE = {"Buildable Queue Size": 0}
MCP_CALLS = []


class FakeMCP(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        msg = json.loads(self.rfile.read(n))
        if "id" not in msg:
            self.send_response(202); self.end_headers(); return
        m, p = msg["method"], msg.get("params") or {}
        if m == "initialize":
            res = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake"}}
        elif m == "tools/call":
            a, name = p.get("arguments") or {}, p["name"]
            MCP_CALLS.append(name)
            if name in ("triggerBuild", "rebuildBuild", "replayBuild", "updateBuild"):
                data = {"error": "write tool called"}
            elif name == "getJobs":
                items = [{"fullName": k, "name": k, "_class": "hudson.model.FreeStyleProject", **v} for k, v in sorted(JOBS.items())]
                page = items[a.get("skip", 0): a.get("skip", 0) + a.get("limit", 10)]
                data = {"message": "ok", "result": page} if page else {"message": "Search completed, but no results were found"}
            elif name == "getBuild":
                b = BUILDS.get((a["jobFullName"], a.get("buildNumber"))) or {}
                data = {"message": "ok", "result": b}
            elif name == "getStatus":
                data = {"message": "ok", "result": dict(QUEUE)}
            else:
                data = {"message": "ok", "result": {}}
            res = {"content": [{"type": "text", "text": json.dumps(data)}]}
        else:
            res = {}
        body = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "sess-1"); self.end_headers(); self.wfile.write(body)


UPDATES = []
SENT = []


class FakeTelegram(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        method = self.path.rsplit("/", 1)[-1]
        if method == "getUpdates":
            res = [u for u in UPDATES if u["update_id"] >= body.get("offset", 0)]
        elif method == "sendMessage":
            SENT.append(body)
            res = {"message_id": 9000 + len(SENT)}
        else:
            res = True
        out = json.dumps({"ok": True, "result": res}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)


mcp_port, tg_port = serve(FakeMCP), serve(FakeTelegram)
CHAT, TOPIC = -1001234, 77
ENV = {**os.environ, "AGENT_OPS_ENV": str(T / "none.env"), "JENKINS_MCP_URL": f"http://127.0.0.1:{mcp_port}/mcp",
       "JENKINS_RO_AUTH": "Basic tok-test-jenkins", "TELEGRAM_BOT_TOKEN": "tok-test-telegram", "TELEGRAM_API": f"http://127.0.0.1:{tg_port}",
       "TELEGRAM_WATCH_CHAT_ID": str(CHAT), "TELEGRAM_WATCH_TOPIC_ID": str(TOPIC), "TELEGRAM_BOT_USERNAME": "our_bot",
       "AGENT_JENKINS_WATCH_DIR": str(T / "jw"), "AGENT_TELEGRAM_DB": str(T / "tg.sqlite3"),
       "AGENT_JOURNAL_DB": str(T / "journal.sqlite3"), "AGENT_OPS_CONFIG_ROOT": str(T / "cfg")}

# config root (AGENT_OPS_CONFIG_ROOT): a person of the example team with a Telegram username, for mentions.
# The owner rules come from the committed example, knowledge/jenkins-owners.yaml.
(T / "cfg").mkdir()
(T / "cfg" / "people.yaml").write_text("""people:
  kenji:
    full_name: Kenji Sato
    telegram_username: kenji_s   # without @
""")


def run(*args):
    r = subprocess.run([sys.executable, "-I", *args], capture_output=True, text=True, env=ENV, timeout=60)
    return r.returncode, r.stdout + r.stderr


def collect():
    return run(str(ROOT / "infra/jenkins-watch/watch.py"), "collect")


def facts():
    return json.loads((T / "jw" / "facts.json").read_text())


# --- 1. quiet Jenkins: idle, nothing posted; durations are learned from finished builds
JOBS.update({"etl-orders-daily": {"color": "blue", "lastBuild": {"number": 10, "result": "SUCCESS"}},
             "pricing-sync-stage": {"color": "blue", "lastBuild": {"number": 5, "result": "SUCCESS"}},
             "nightly-warehouse-load": {"color": "blue", "lastBuild": {"number": 3, "result": "SUCCESS"}},
             "old-thing": {"color": "disabled", "lastBuild": {"number": 1}}})
BUILDS[("nightly-warehouse-load", 3)] = {"number": 3, "building": False, "duration": 20 * 60_000, "result": "SUCCESS"}
rc, out = collect()
check(rc == 1 and "idle" in out, "quiet Jenkins: collect is idle (exit 1), so no Claude run", out)

# --- 2. a failure and a stuck build → work, with project and owner from knowledge/jenkins-owners.yaml
JOBS["etl-orders-daily"] = {"color": "red", "lastBuild": {"number": 11, "result": "FAILURE"}}
JOBS["nightly-warehouse-load"] = {"color": "blue_anime", "lastBuild": {"number": 4}}
BUILDS[("nightly-warehouse-load", None)] = {"number": 4, "building": True, "timestamp": NOW_MS - 90 * 60_000}
rc, out = collect()
f = facts()
ev = {e["job"]: e for e in f["events"]}
check(rc == 0 and set(ev) == {"etl-orders-daily", "nightly-warehouse-load"}, "new failure and stuck build are events", (rc, out, ev))
w = ev.get("etl-orders-daily", {})
check(w.get("kind") == "failed" and w.get("result") == "FAILURE" and w.get("project") == "ANL" and w.get("owner") == "kenji",
      "failure: result, project ANL, owner from knowledge/jenkins-owners.yaml", w)
s = ev.get("nightly-warehouse-load", {})
check(s.get("kind") == "stuck" and s.get("running_min") == 90 and s.get("usual_min") == 20.0 and s.get("project") == "ANL" and s.get("owner") == "kenji",
      "stuck: running 90 min against a usual 20 (more than 2x and 30 min)", s)
check(not any(c in MCP_CALLS for c in ("triggerBuild", "rebuildBuild", "replayBuild", "updateBuild")),
      "read-only: no Jenkins write tool was called", MCP_CALLS)

# --- 3. same state again → idle (an unchanged incident isn't reposted)
rc, out = collect()
check(rc == 1, "unchanged incidents: idle", out)

# --- 4. the Jenkins bot's alert in the topic is stored by poll.py and linked to the incident
UPDATES.extend([
    {"update_id": 1, "message": {"message_id": 501, "message_thread_id": TOPIC, "date": int(time.time()),
                                 "chat": {"id": CHAT, "type": "supergroup", "title": "Ops"},
                                 "from": {"id": 42, "is_bot": True, "username": "jenkins_notify_bot"},
                                 "text": "❌ Build failed: etl-orders-daily #11"}},
    {"update_id": 2, "message": {"message_id": 7, "date": int(time.time()), "chat": {"id": 555, "type": "private"},
                                 "from": {"id": 555, "is_bot": False, "username": "someone"}, "text": "private hello"}},
])
rc, out = run(str(ROOT / "infra/telegram/poll.py"))
check(rc == 0 and json.loads(out.strip().splitlines()[-1]) == {"updates": 2, "stored": 1},
      "poller: reads updates, stores only the watched chat's message", out)
rc, out = run(str(ROOT / "infra/telegram/poll.py"), "chats")
chats = json.loads(out)
check(any(c["chat_id"] == CHAT and c["thread_id"] == TOPIC and "jenkins_notify_bot" in c["bot_senders"] for c in chats)
      and not any(c["chat_id"] == 555 for c in chats), "poller: `chats` lists group topics and their bots (not private chats)", chats)
rc, out = run(str(ROOT / "infra/telegram/poll.py"))
check(json.loads(out.strip().splitlines()[-1])["updates"] == 0, "poller: an update is read once (offset kept)", out)
rc, out = collect()
f = facts()
check(rc == 0 and f["alerts"] and f["alerts"][0]["message_id"] == 501
      and f["alerts"][0]["jobs"] == ["etl-orders-daily"], "collect: the new alert wakes the agent and names its job", f["alerts"])

# --- 5. post: one summary in the topic, owners mentioned for attention/stuck, a reply to the alert
(T / "jw" / "decisions.json").write_text(json.dumps({"headline": "1 needs attention, 1 stuck", "items": [
    {"job": "etl-orders-daily", "verdict": "attention", "project": "ANL", "owner": "kenji",
     "line": "The load step fails on a missing column since commit abc123.", "reply_to": [501],
     "reply": "Real failure: a missing column after this morning's SQL change; needs a fix before the next run."},
    {"job": "nightly-warehouse-load", "verdict": "stuck", "project": "ANL", "owner": "kenji",
     "line": "Running 90 min (usually 20); waiting on a table lock.", "reply_to": [], "reply": ""}]}))
rc, out = run(str(ROOT / "infra/jenkins-watch/watch.py"), "post")
summary = SENT[0] if SENT else {}
check(rc == 0 and len(SENT) == 2, "post: one summary and one reply sent", (rc, out, SENT))
check(summary.get("chat_id") == CHAT and summary.get("message_thread_id") == TOPIC and summary.get("parse_mode") == "HTML"
      and "Needs attention" in summary.get("text", "") and "Stuck" in summary.get("text", ""), "summary goes to the topic, grouped", summary)
check("@kenji_s" in summary.get("text", ""), "summary: the owner is mentioned by their people.yaml Telegram username", summary.get("text"))
reply = SENT[1] if len(SENT) > 1 else {}
check(reply.get("reply_parameters", {}).get("message_id") == 501 and "missing column" in reply.get("text", ""),
      "reply: answers the Jenkins bot's alert", reply)
st = json.loads((T / "jw" / "state.json").read_text())
check(st["incidents"]["etl-orders-daily"]["messages"] == [9001]
      and st["incidents"]["etl-orders-daily"]["verdict"] == "attention", "state: our message id and verdict kept", st["incidents"])
check(not (T / "jw" / "decisions.json").exists(), "post: decisions are consumed (not sent twice)")
try:
    rows = sqlite3.connect(str(T / "journal.sqlite3")).execute(
        "select system, target_id, reversible from events where action = 'jenkins-report'").fetchall()
except sqlite3.Error as e:
    rows = [str(e)]
check(rows == [("telegram", "9001", "no")], "post: the report is in the action journal (irreversible)", rows)
rc, out = run(str(ROOT / "infra/jenkins-watch/watch.py"), "post")
check(len(SENT) == 2, "post again without new decisions: nothing sent", SENT[2:])
check("<" not in summary.get("text", "").replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
      .replace("<a ", "").replace("</a>", "") or "tg://user" in summary.get("text", ""), "summary: only safe HTML")

# --- 6. recovery: the job goes green → `recovered` event (it was posted), then the incident closes
JOBS["etl-orders-daily"] = {"color": "blue", "lastBuild": {"number": 12, "result": "SUCCESS"}}
BUILDS[("etl-orders-daily", 12)] = {"number": 12, "building": False, "duration": 70 * 60_000}
rc, out = collect()
ev = {e["job"]: e for e in facts()["events"]}
r = ev.get("etl-orders-daily", {})
check(rc == 0 and r.get("event") == "recovered" and r.get("messages") == [9001], "recovered: event carries our earlier message to reply to", ev)
st = json.loads((T / "jw" / "state.json").read_text())
check("etl-orders-daily" not in st["incidents"], "recovered: the incident is closed")

# --- 7. a failure that recovers before anyone heard of it: no recovered event
JOBS["pricing-sync-stage"] = {"color": "red", "lastBuild": {"number": 6, "result": "FAILURE"}}
collect()
JOBS["pricing-sync-stage"] = {"color": "blue", "lastBuild": {"number": 7, "result": "SUCCESS"}}
BUILDS[("pricing-sync-stage", 7)] = {"number": 7, "building": False, "duration": 60_000}
rc, out = collect()
check(not any(e["job"] == "pricing-sync-stage" for e in facts()["events"]), "a blip nobody was told about closes quietly", facts()["events"])

# --- 8. through the precheck (run-agent.sh's path): same exit codes as collect
r = subprocess.run(["bash", str(ROOT / "infra/bin/precheck.sh"), "jenkins-watch", "1970-01-01T00:00:00Z"],
                   capture_output=True, text=True, env=ENV, timeout=60)
check(r.returncode == 1 and "idle" in r.stdout, "precheck.sh jenkins-watch: idle when nothing is new", (r.returncode, r.stdout, r.stderr))

# --- 9. Jenkins unreachable: collect fails (exit 3), never "idle"
bad = {**ENV, "JENKINS_MCP_URL": "http://127.0.0.1:9/mcp"}
r = subprocess.run([sys.executable, "-I", str(ROOT / "infra/jenkins-watch/watch.py"), "collect"], capture_output=True, text=True, env=bad, timeout=60)
check(r.returncode == 3 and "precheck failed" in r.stderr, "Jenkins unreachable: the check fails (3), it's never 'nothing new'", r.stderr)
r = subprocess.run(["bash", str(ROOT / "infra/bin/precheck.sh"), "jenkins-watch", "1970-01-01T00:00:00Z"],
                   capture_output=True, text=True, env={**bad, "JENKINS_MCP_URL": ""}, timeout=60)
check(r.returncode == 3 and "JENKINS_MCP_URL" in r.stderr, "precheck.sh jenkins-watch: not set up → check failed (3)", (r.returncode, r.stderr))

print(f"\n{'all passed' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
