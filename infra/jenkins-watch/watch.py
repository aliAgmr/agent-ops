#!/usr/bin/env python3
"""Jenkins watcher: finds failed and stuck jobs, and posts an assessed summary to the team's Telegram
group, replying to the alerts a Jenkins notifier bot already posted there. Read-only on Jenkins (the
agents' read-only user, through the Jenkins MCP server); it never runs, stops or edits a job.

  watch.py collect     read every job and the watched topic's new messages → facts.json
                       exit 0 = something new to assess (run the agent), 1 = nothing new (skip)
  watch.py post        send what the agent decided (decisions.json) and remember the message ids
  watch.py status      the open incidents

One run every 15 minutes (service/jenkins-watch, run-agent.sh): `collect` is its precheck
(infra/bin/precheck.py; exit 3 = the check failed), so a quiet Jenkins costs no Claude tokens. The
agent reads the logs, decides whether a failure needs attention or is a passing blip (a dropped
connection that the next build fixed), and writes decisions.json; `post` is the only thing that
talks to Telegram.

Incidents (state.json), one per job:
  failed  the last finished build failed / was unstable / was aborted
  stuck   a build has run longer than STUCK_FACTOR × its usual duration (median of the builds this
          watcher saw finish; at least STUCK_MIN minutes)
  queue   builds have been waiting to start for two checks in a row (Jenkins exposes only a count)
Events: new, again (a newer build failed too), recovered (the job is green again), and new alerts
from other bots in the topic. Only events wake the agent; an unchanged incident is not reposted.

Settings (env file, infra/env.template): JENKINS_MCP_URL, JENKINS_RO_AUTH, TELEGRAM_BOT_TOKEN,
TELEGRAM_WATCH_CHAT_ID, TELEGRAM_WATCH_TOPIC_ID, TELEGRAM_BOT_USERNAME, JENKINS_WATCH_STUCK_MIN (30),
JENKINS_WATCH_STUCK_FACTOR (2). Owners: knowledge/jenkins-owners.yaml; mentions: people.yaml.
Standard library only.
"""
from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra"))
import adapters as A  # noqa: E402  (env file, YAML and people.yaml readers)

DIR = Path(os.environ.get("AGENT_JENKINS_WATCH_DIR", "~/.local/share/agent-ops/jenkins-watch")).expanduser()
TG_DB = Path(os.environ.get("AGENT_TELEGRAM_DB", "~/.local/share/agent-ops/telegram/messages.sqlite3")).expanduser()
FAILED = {"red", "yellow", "aborted"}           # Jenkins ball colours (plus _anime while running)


def load_json(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def save_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(p)


# ------------------------------------------------------------------------------- Jenkins (MCP)

class JenkinsMCP:
    """The few read-only calls of the Jenkins MCP server (streamable HTTP, JSON-RPC)."""

    def __init__(self, url: str, auth: str):
        self.url, self.auth, self.sid = url, auth, None
        self._rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "jenkins-watch", "version": "1"}})
        self._rpc("notifications/initialized", notify=True)

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None, notify: bool = False) -> Any:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "Authorization": self.auth}
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        body: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            body["id"] = 1
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=90) as r:  # noqa: S310 (the configured Jenkins MCP URL)
            txt = r.read().decode()
            self.sid = r.headers.get("Mcp-Session-Id") or self.sid
        if notify:
            return None
        m = re.search(r"^data: (.*)$", txt, re.M)
        msg = json.loads(m.group(1) if m else txt)
        if "error" in msg:
            raise RuntimeError(f"Jenkins MCP {method}: {msg['error'].get('message')}")
        return msg["result"]

    def call(self, tool: str, **args) -> Any:
        res = self._rpc("tools/call", {"name": tool, "arguments": args})
        text = (res.get("content") or [{}])[0].get("text", "")
        try:
            data = json.loads(text)
        except ValueError:
            return text
        return data.get("result", data) if isinstance(data, dict) else data

    def jobs(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        skip = 0
        while skip < 5000:
            page = self.call("getJobs", skip=skip, limit=10)
            if not isinstance(page, list) or not page:
                break
            out += page
            skip += len(page)
        return out

    def build(self, job: str, number: Optional[int] = None) -> Dict[str, Any]:
        b = self.call("getBuild", jobFullName=job, **({"buildNumber": number} if number else {}))
        return b if isinstance(b, dict) else {}

    def status(self) -> Dict[str, Any]:
        s = self.call("getStatus")
        return s if isinstance(s, dict) else {}


# ------------------------------------------------------------------------------- owners / project

def owner_rules() -> List[Dict[str, str]]:
    """knowledge/jenkins-owners.yaml (in the config root, else this checkout): the ordered rules."""
    for base in (A.repo_root(), ROOT):
        f = base / "knowledge" / "jenkins-owners.yaml"
        if f.is_file():
            return [r for r in ((A.load_yaml(f) or {}).get("rules") or []) if isinstance(r, dict)]
    return []


def classify(job: str, rules: List[Dict[str, str]]) -> Dict[str, str]:
    for r in rules:
        if r.get("match") and re.search(str(r["match"]), job, re.I):
            return {"project": str(r.get("project") or "other"), "owner": str(r.get("owner") or ""), "rule": str(r["match"])}
    return {"project": "other", "owner": "", "rule": ""}


# ----------------------------------------------------------------------------------- collect

def collect(jm: JenkinsMCP, state: Dict[str, Any], rules: List[Dict[str, str]], env: Dict[str, str],
            now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    stuck_min = float(env.get("JENKINS_WATCH_STUCK_MIN", "30")) * 60
    factor = float(env.get("JENKINS_WATCH_STUCK_FACTOR", "2"))
    durations: Dict[str, List[int]] = state.setdefault("durations", {})
    seen_builds: Dict[str, int] = state.setdefault("seen_builds", {})
    incidents: Dict[str, Dict[str, Any]] = state.setdefault("incidents", {})
    events: List[Dict[str, Any]] = []
    jobs = jm.jobs()
    current: Dict[str, Dict[str, Any]] = {}

    for j in jobs:
        name, color = j.get("fullName") or j.get("name"), str(j.get("color") or "")
        if not name or color in ("disabled", "notbuilt") or j.get("_class", "").endswith("Folder"):
            continue
        base, running = color.replace("_anime", ""), color.endswith("_anime")
        last = j.get("lastBuild") or {}
        # remember durations of builds that finished since we last looked (for "usual duration")
        if not running and last.get("number") and seen_builds.get(name) != last.get("number"):
            seen_builds[name] = last["number"]
            if base == "blue":
                b = jm.build(name, last["number"])
                if b.get("duration"):
                    durations.setdefault(name, []).append(int(b["duration"]))
                    durations[name] = durations[name][-10:]
        if base in FAILED:
            ref = last.get("number") if not running else None
            current[name] = {"kind": "failed", "result": {"red": "FAILURE", "yellow": "UNSTABLE", "aborted": "ABORTED"}[base],
                             "build": ref, "running_again": running}
        if running:
            b = jm.build(name)
            started = (b.get("timestamp") or 0) / 1000
            usual = durations.get(name)
            if not usual:
                lc = jm.build(name, (last.get("number") or 1) - 1) if last.get("number") else {}
                if lc.get("duration") and not lc.get("building"):
                    usual = [int(lc["duration"])]
                    durations[name] = usual
            if usual and started:
                limit = max(stuck_min, factor * statistics.median(usual) / 1000)
                ran = now - started
                if ran > limit:
                    current[name] = {"kind": "stuck", "build": b.get("number"), "running_min": round(ran / 60),
                                     "usual_min": round(statistics.median(usual) / 60000, 1)}

    st = jm.status()
    queued = int(st.get("Buildable Queue Size") or 0)
    if queued > 0 and state.get("queue_seen_at"):
        current["(queue)"] = {"kind": "queue", "waiting": queued,
                              "since_min": round((now - state["queue_seen_at"]) / 60)}
    state["queue_seen_at"] = (state.get("queue_seen_at") or now) if queued > 0 else None

    for name, cur in current.items():
        old = incidents.get(name)
        info = classify(name, rules) if name != "(queue)" else {"project": "jenkins", "owner": "", "rule": ""}
        if not old:
            incidents[name] = {**cur, **info, "since": now, "first_build": cur.get("build"), "messages": [], "alerts": []}
            events.append({"event": "new", "job": name, **incidents[name]})
        else:
            changed = cur["kind"] != old["kind"] or (cur.get("build") and cur.get("build") != old.get("build"))
            old.update({k: v for k, v in cur.items()})
            if changed:
                events.append({"event": "again", "job": name, **old})
    for name in [n for n in incidents if n not in current]:
        inc = incidents.pop(name)
        if inc.get("messages") or inc.get("alerts"):     # tell the group only about what it heard of
            events.append({"event": "recovered", "job": name, **inc})

    alerts = new_alerts(env, state)
    for a in alerts:
        a["jobs"] = [n for n in list(incidents) + [e["job"] for e in events] if n != "(queue)" and n.lower() in (a["text"] or "").lower()]
        for n in a["jobs"]:
            if n in incidents and a["message_id"] not in incidents[n]["alerts"]:
                incidents[n]["alerts"].append(a["message_id"])

    return {"checked_at": int(now), "jobs": len(jobs), "open": len(incidents),
            "events": events, "alerts": alerts,
            "incidents": {k: {kk: vv for kk, vv in v.items()} for k, v in incidents.items()}}


def new_alerts(env: Dict[str, str], state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Messages other bots posted in the watched topic since the last check (poll.py stores them)."""
    chat, topic = env.get("TELEGRAM_WATCH_CHAT_ID"), env.get("TELEGRAM_WATCH_TOPIC_ID")
    if not chat or not TG_DB.is_file():
        return []
    c = sqlite3.connect(str(TG_DB), timeout=10)
    c.row_factory = sqlite3.Row
    last = int(state.get("last_alert_id") or 0)
    sql = "select message_id, date, from_username, text from messages where chat_id=? and from_is_bot=1 and message_id>?"
    args: List[Any] = [int(chat), last]
    if topic:
        sql += " and thread_id=?"
        args.append(int(topic))
    me = (env.get("TELEGRAM_BOT_USERNAME") or "").lstrip("@").lower()
    rows = [dict(r) for r in c.execute(sql + " order by message_id", args) if (r["from_username"] or "").lower() != me]
    if rows:
        state["last_alert_id"] = rows[-1]["message_id"]
    return [{"message_id": r["message_id"], "date": r["date"], "from": r["from_username"], "text": (r["text"] or "")[:1500]}
            for r in rows]


# ------------------------------------------------------------------------------------- post

def people() -> Dict[str, Dict[str, Any]]:
    """people.yaml `people:` → key: {full_name, telegram_username, telegram_chat_id, …}."""
    return {str(k): (v or {}) for k, v in ((A.load_people().get("people") or {}).items())}


def mention(owner: str, team: Dict[str, Dict[str, Any]]) -> str:
    p = team.get(owner) or {}
    user = str(p.get("telegram_username") or "").lstrip("@")
    name = html.escape(str(p.get("full_name") or owner).split(" ")[0])
    if user:
        return f"@{html.escape(user)}"
    if str(p.get("telegram_chat_id") or "").isdigit():               # a person's private chat id
        return f'<a href="tg://user?id={p["telegram_chat_id"]}">{name}</a>'
    return name


SECTIONS = [("attention", "Needs attention"), ("stuck", "Stuck"), ("transient", "Passing blips (no action)"),
            ("recovered", "Recovered")]


def compose(decisions: Dict[str, Any], team: Dict[str, Dict[str, Any]], now: Optional[float] = None) -> str:
    items = [d for d in decisions.get("items", []) if d.get("post", True)]
    when = time.strftime("%H:%M UTC", time.gmtime(now or time.time()))
    lines = [f"<b>Jenkins check · {when}</b>"]
    if decisions.get("headline"):
        lines.append(html.escape(decisions["headline"]))
    for key, title in SECTIONS:
        group = [d for d in items if d.get("verdict") == key]
        if not group:
            continue
        lines.append(f"\n<b>{title}</b>")
        for d in group:
            who = f" · {mention(d['owner'], team)}" if d.get("owner") and key in ("attention", "stuck") else ""
            proj = f" [{html.escape(d['project'])}]" if d.get("project") and d["project"] != "other" else ""
            lines.append(f"• <code>{html.escape(d['job'])}</code>{proj}: {html.escape(d.get('line', ''))}{who}")
    text = "\n".join(lines)
    return text[:4000] + ("…" if len(text) > 4000 else "")


def send(token: str, chat: str, text: str, topic: Optional[str] = None, reply_to: Optional[int] = None) -> int:
    api = os.environ.get("TELEGRAM_API", "https://api.telegram.org").rstrip("/")
    body: Dict[str, Any] = {"chat_id": int(chat), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if topic:
        body["message_thread_id"] = int(topic)
    if reply_to:
        body["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
    req = urllib.request.Request(f"{api}/bot{token}/sendMessage", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (Telegram)
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram sendMessage: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}") from None
    if not res.get("ok"):
        raise RuntimeError(f"Telegram sendMessage: {res.get('description')}")
    return int(res["result"]["message_id"])


def post(state: Dict[str, Any], decisions: Dict[str, Any], env: Dict[str, str]) -> Dict[str, Any]:
    token, chat, topic = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_WATCH_CHAT_ID"), env.get("TELEGRAM_WATCH_TOPIC_ID")
    if not token or not chat:
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_WATCH_CHAT_ID must be set")
    team = people()
    items = [d for d in decisions.get("items", []) if d.get("post", True)]
    incidents = state.setdefault("incidents", {})
    sent: Dict[str, Any] = {"summary": None, "replies": []}
    if items:
        mid = send(token, chat, compose(decisions, team), topic)
        sent["summary"] = mid
        for d in items:
            if d["job"] in incidents:
                incidents[d["job"]].setdefault("messages", []).append(mid)
                incidents[d["job"]]["verdict"] = d.get("verdict")
    for d in decisions.get("items", []):
        if not d.get("reply"):
            continue
        targets = list(d.get("reply_to") or [])
        for m in targets[:5]:                       # loop guard: a few replies per job per run
            text = f"<b>{html.escape(d['job'])}</b>: {html.escape(d['reply'])}"
            if d.get("owner") and d.get("verdict") in ("attention", "stuck"):
                text += f" · {mention(d['owner'], team)}"
            sent["replies"].append({"to": m, "id": send(token, chat, text, topic, reply_to=m)})
            time.sleep(1)                           # Telegram's bot-to-bot guidance: no reply bursts
    record(sent, decisions)
    return sent


def record(sent: Dict[str, Any], decisions: Dict[str, Any]) -> None:
    """Action journal: what the watcher posted (irreversible, so the hint says so)."""
    journal = ROOT / "infra" / "journal" / "journal.py"
    if not journal.is_file() or not (sent.get("summary") or sent.get("replies")):
        return
    jobs = ", ".join(sorted({d["job"] for d in decisions.get("items", [])}))[:300]
    subprocess.run([sys.executable, "-I", str(journal), "record", "--agent", os.environ.get("AGENT_NAME") or "jenkins-watch",
                    "--system", "telegram", "--action", "jenkins-report", "--target-type", "message",
                    "--target-id", str(sent.get("summary") or ""), "--reversible", "no",
                    "--summary", f"Jenkins report to the group ({len(sent['replies'])} replies): {jobs}",
                    "--hint", "A sent Telegram message can't be unsent: post a correction in the topic."],
                   capture_output=True, timeout=30)


# ------------------------------------------------------------------------------------------- CLI

def main(argv: List[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    env = A.env_values()
    state_file, facts_file, decisions_file = DIR / "state.json", DIR / "facts.json", DIR / "decisions.json"
    state = load_json(state_file, {})
    if cmd == "collect":
        if not env.get("JENKINS_MCP_URL") or not env.get("JENKINS_RO_AUTH"):
            print("precheck failed: JENKINS_MCP_URL / JENKINS_RO_AUTH not set", file=sys.stderr)
            return 3
        try:
            facts = collect(JenkinsMCP(env["JENKINS_MCP_URL"], env["JENKINS_RO_AUTH"]), state, owner_rules(), env)
        except (OSError, urllib.error.URLError, RuntimeError) as e:
            print(f"precheck failed: Jenkins MCP: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
            return 3
        save_json(facts_file, facts)
        save_json(state_file, state)
        try:
            decisions_file.unlink()
        except OSError:
            pass
        n = len(facts["events"]) + len(facts["alerts"])
        print(f"work: {len(facts['events'])} Jenkins event(s), {len(facts['alerts'])} new alert(s)" if n else
              f"idle: {facts['jobs']} jobs, {facts['open']} open incident(s), nothing new")
        return 0 if n else 1
    if cmd == "post":
        decisions = load_json(decisions_file, None)
        if decisions is None:
            print("no decisions.json: nothing to post")
            return 0
        out = post(state, decisions, env)
        save_json(state_file, state)
        decisions_file.replace(DIR / "decisions.posted.json")
        print(json.dumps(out))
        return 0
    if cmd == "status":
        print(json.dumps({k: {kk: v.get(kk) for kk in ("kind", "verdict", "project", "owner", "build", "since")}
                          for k, v in state.get("incidents", {}).items()}, indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
