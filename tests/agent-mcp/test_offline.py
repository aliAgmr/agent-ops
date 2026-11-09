# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==1.30.0", "pyyaml==6.0.2"]
# ///
"""Offline tests for infra/agent-mcp/server.py: a fake tracker adapter, a fake `claude`, the real
server over streamable HTTP and the real MCP client. Run: bash tests/agent-mcp/run-offline.sh"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BIL = "00000000-0000-4000-8000-000000000001"     # projects.yaml: Acme Billing
OTHER = "11111111-2222-3333-4444-555555555555"

# token -> (user id, email, [(project id, is_member)])
USERS = {
    "tok-alice-0001": ("u-alice", "alice@example.com", [(BIL, True)]),
    "tok-bob-000002": ("u-bob", "bob@example.com", [(BIL, True)]),
    "tok-carol-0003": ("u-carol", "carol@example.com", [(OTHER, True), (BIL, False)]),  # admin, not a member
}
TRACKER_CALLS = {"n": 0}


class FakeTracker:
    """The tracker adapter's identity calls (infra/adapters/README.md), for the given token."""

    def __init__(self, token):
        self.user = USERS.get(token)

    def whoami(self):
        TRACKER_CALLS["n"] += 1
        if not self.user:
            raise RuntimeError("HTTP 403: bad key")
        return {"id": self.user[0], "email": self.user[1], "display_name": self.user[1].split("@")[0]}

    def projects(self):
        TRACKER_CALLS["n"] += 1
        return [{"id": pid, "identifier": pid[:4], "name": pid[:4], "is_member": m} for pid, m in self.user[2]]

    def get_item(self, pid, wid):
        return {"id": wid, "key": "BIL-1", "state": "st-1", "label_ids": [], "assignees": []}

    def activities(self, pid, wid):
        return []


TG_SENT: list[dict] = []
TG_TOKEN = "123456:fake-telegram-bot-token"


class FakeTelegram(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        ok_path = self.path == f"/bot{TG_TOKEN}/sendMessage"
        if ok_path and str(body.get("chat_id")) != "500":
            TG_SENT.append(body)
            out, code = {"ok": True, "result": {"message_id": len(TG_SENT)}}, 200
        else:
            out, code = {"ok": False, "description": "Bad Request: chat not found"}, 400
        data = json.dumps(out).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(data)


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


TMP = Path(tempfile.mkdtemp(prefix="agent-mcp-test-"))
PLANTED = "planted-secret-VALUE-42"
PG_PASS = "pgpass-12345678"
server_port, tg_port = free_port(), free_port()
(TMP / "env").write_text(f"FAKE_PLANTED_SECRET={PLANTED}\nPG_RO_URL=postgresql://app_ro:{PG_PASS}@db.example.com:5432/app\n"
                         f"ANTHROPIC_API_KEY=\nSHORT=abc\nTELEGRAM_BOT_TOKEN={TG_TOKEN}\n")
(TMP / "people.yaml").write_text("""people:
  alice: {full_name: Alice Archer, role: data, email: Alice@Example.com, telegram_username: alice_tg, telegram_chat_id: 111}
  bob: {full_name: Bob Builder, role: backend, email: bob@example.com, telegram_username: bobb, telegram_chat_id: 222}
  dan: {full_name: Dan Dark, role: frontend, email: dan@example.com, telegram_chat_id: }
  sam1: {full_name: Sam One, role: design, email: s1@example.com, telegram_chat_id: 333}
  sam2: {full_name: Sam Two, role: design, email: s2@example.com, telegram_chat_id: 444}
  zed: {full_name: Zed Broken, role: devops, email: zed@example.com, telegram_chat_id: 500}
""")
(TMP / "routing.yaml").write_text("""teams:
  backend: {person: bob}
  data: {person: alice}
  design: {people: [sam1, sam2]}
projects:
  PRC:
    teams:
      backend: {people: [bob, alice]}
""")
(TMP / "x.token").write_text("tracker-token-file-value-99\n")
profile = TMP / "profile"; profile.mkdir(); (profile / ".mcp.json").write_text('{"mcpServers": {}}')
fake = TMP / "claude"; fake.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{HERE / 'fake_claude.py'}' \"$@\"\n"); fake.chmod(0o755)
os.environ.update({
    "AGENT_MCP_PORT": str(server_port), "AGENT_MCP_DATA_DIR": str(TMP / "data"),
    "AGENT_MCP_PROFILE_DIR": str(profile), "AGENT_MCP_PROJECTS": str(ROOT / "projects.yaml"),
    "AGENT_OPS_ENV": str(TMP / "env"), "AGENT_MCP_SECRET_GLOBS": str(TMP / "*.token"),
    "AGENT_MCP_CLAUDE": str(fake), "AGENT_MCP_TRACKER_URL": "https://tracker.example.com",
    "AGENT_MCP_TRACKER_WORKSPACE": "acme", "FAKE_CLAUDE_TIMES": str(TMP / "times"),
    "FAKE_PG_PASSWORD": PG_PASS,
    "AGENT_MCP_PEOPLE": str(TMP / "people.yaml"), "AGENT_MCP_ROUTING": str(TMP / "routing.yaml"),
    "AGENT_MCP_TELEGRAM_API": f"http://127.0.0.1:{tg_port}", "AGENT_MCP_MSG_PER_HOUR": "6",
})
(TMP / "lineage.json").write_text(json.dumps({"built_at": int(time.time()), "nodes": [
    {"id": "table:analytics.ledger"}, {"id": "table:analytics.v_revenue_daily", "owner": "kenji"},
    {"id": "route:GET /invoices/summary"}, {"id": "page:/revenue", "name": "Revenue dashboard", "owner": "noor"}],
    "edges": [{"from": "table:analytics.ledger", "to": "table:analytics.v_revenue_daily", "kind": "feeds"},
              {"from": "table:analytics.v_revenue_daily", "to": "route:GET /invoices/summary", "kind": "read by"},
              {"from": "route:GET /invoices/summary", "to": "page:/revenue", "kind": "shown on"}]}))
os.environ["AGENT_LINEAGE_CACHE"] = str(TMP / "lineage.json")
sys.path.insert(0, str(ROOT / "infra" / "agent-mcp"))
import server  # noqa: E402
import uvicorn  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402

URL = f"http://127.0.0.1:{server_port}/mcp"
fails: list[str] = []
LOGGED: list[str] = []


class _Capture(__import__("logging").Handler):
    def emit(self, record):
        LOGGED.append(record.getMessage())


__import__("logging").getLogger().addHandler(_Capture())
__import__("logging").getLogger().setLevel("INFO")


APP: list = []
app_state = lambda: APP[0].state_


def check(cond: bool, label: str, detail: str = "") -> None:
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  [{detail}]"))
    if not cond:
        fails.append(label)


class Client:
    def __init__(self, token: str | None, header: str = "Authorization"):
        self.headers = {} if token is None else ({"Authorization": f"Bearer {token}"} if header == "Authorization" else {header: token})

    async def call(self, tool: str, **args):
        async with streamablehttp_client(URL, headers=self.headers) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool(tool, args)
                text = res.content[0].text if res.content else ""
                if res.isError:
                    return {"_error": text}
                if res.structuredContent is not None:
                    sc = res.structuredContent
                    return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
                return json.loads(text)


async def raw_status(headers: dict) -> int:
    import httpx
    async with httpx.AsyncClient() as c:
        r = await c.post(URL, headers={**headers, "Accept": "application/json, text/event-stream",
                                       "Content-Type": "application/json"},
                         json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "t", "version": "0"}}})
        return r.status_code


async def until_done(c: Client, ticket: str) -> dict:
    for _ in range(40):
        a = await c.call("get_answer", ticket=ticket, wait_seconds=5)
        if a.get("status") in ("done", "error") or "_error" in a:
            return a
    return a


async def tests() -> None:
    alice, bob, carol = Client("tok-alice-0001"), Client("tok-bob-000002"), Client("tok-carol-0003")

    # --- auth
    check(await raw_status({}) == 401, "no token -> 401")
    check(await raw_status({"Authorization": "Bearer nope-nope"}) == 401, "bad token -> 401")
    check(await raw_status({"X-API-Key": "tok-alice-0001"}) == 200, "X-API-Key accepted")
    import httpx
    async with httpx.AsyncClient() as hc:
        check((await hc.get(f"http://127.0.0.1:{server_port}/healthz")).status_code == 200, "healthz open")
    projects = await alice.call("list_projects")
    check(isinstance(projects, list) and [p["project"] for p in projects] == ["BIL"], "member sees BIL", str(projects))
    check(await carol.call("list_projects") == [], "non-member (admin listing, is_member false) sees no project")
    r = await carol.call("create_conversation", project="BIL", title="x")
    check("_error" in r and "not a member" in r["_error"], "non-member refused for BIL", str(r))
    r = await alice.call("create_conversation", project="NOPE", title="x")
    check("_error" in r, "unknown project refused")
    n = TRACKER_CALLS["n"]
    await alice.call("list_projects")
    check(TRACKER_CALLS["n"] == n, "validation cached (no task-board call on repeat)")

    # --- conversations + privacy
    conv = await alice.call("create_conversation", project="bil", title="resolveLanguage")
    cid = conv["conversation_id"]
    check(conv["project"] == "BIL" and conv["status"] == "open", "create_conversation", str(conv))
    check(any(c["conversation_id"] == cid for c in await alice.call("list_conversations", project="BIL")), "owner lists it")
    check(await bob.call("list_conversations") == [], "other user lists nothing")
    r = await bob.call("ask", conversation_id=cid, question="hi")
    check("_error" in r and "not found" in r["_error"], "other user cannot ask in it", str(r))
    r = await bob.call("rename_conversation", conversation_id=cid, title="mine now")
    check("_error" in r, "other user cannot rename it")
    r = await alice.call("rename_conversation", conversation_id=cid, title="resolveLanguage (backend)")
    check(r.get("title") == "resolveLanguage (backend)", "owner renames it")

    # --- ticket flow, first ask then follow-up resumes the stored session
    t1 = await alice.call("ask", conversation_id=cid, question="first question", depth="quick")
    check(t1.get("status") == "queued" and t1.get("ticket"), "ask returns ticket at once", str(t1))
    r = await bob.call("get_answer", ticket=t1["ticket"], wait_seconds=1)
    check("_error" in r, "other user cannot read the ticket")
    a1 = await until_done(alice, t1["ticket"])
    check(a1.get("status") == "done", "first answer done", str(a1))
    ans1 = a1.get("answer", "")
    session = ans1.split("session=")[1].split()[0] if "session=" in ans1 else ""
    check("mode=--session-id" in ans1 and len(session) == 36, "first ask uses --session-id", ans1)
    check("effort=low" in ans1 and "max_turns=20" in ans1 and "model=claude-opus-5-5" in ans1, "depth quick -> low effort, opus 5.5", ans1)
    check(f"cwd={profile.resolve()}" in ans1 or f"cwd={profile}" in ans1, "runs in the profile dir", ans1)
    check("mcp_timeout=120000" in ans1, "MCP_TIMEOUT=120000")
    check(a1.get("sources") == ["fake/path.ts:1", "SQL: select 1"] and "## Sources" not in ans1, "sources split out", str(a1.get("sources")))
    t2 = await alice.call("ask", conversation_id=cid, question="follow-up")
    a2 = await until_done(alice, t2["ticket"])
    check(f"mode=--resume session={session}" in a2.get("answer", ""), "follow-up passes --resume <stored session>", a2.get("answer", ""))
    check("effort=medium" in a2.get("answer", ""), "default depth -> medium effort")

    # --- per-conversation serialization; other conversations run concurrently
    times = TMP / "times"
    times.write_text("")
    conv2 = (await alice.call("create_conversation", project="BIL", title="parallel"))["conversation_id"]
    t0 = time.time()
    ts = [await alice.call("ask", conversation_id=cid, question=f"SLOW {i}") for i in range(2)]
    ts.append(await alice.call("ask", conversation_id=conv2, question="SLOW other"))
    results = [await until_done(alice, t["ticket"]) for t in ts]
    spans = sorted((float(a), float(b), s) for a, b, s in (l.split() for l in times.read_text().split("\n") if l.strip()))
    check(all(r.get("status") == "done" for r in results) and len(spans) == 3, "three slow asks done", str(results))
    # Runs in the same conversation (same session) never overlap; the run in conv2 overlaps at least
    # one of them. Compare by session, not by a total overlap count: the conv2 run can straddle the
    # hand-off between the two serialized runs and overlap both.
    pairs = [(x, y) for i, x in enumerate(spans) for y in spans[i + 1:]]
    overlap = lambda x, y: x[1] > y[0] and y[1] > x[0]
    same = [overlap(x, y) for x, y in pairs if x[2] == y[2]]
    other = [overlap(x, y) for x, y in pairs if x[2] != y[2]]
    check(len(same) == 1 and not any(same) and any(other), "same conversation serialized, different conversations concurrent", str(spans))
    check(time.time() - t0 < 6, "total time shows concurrency", f"{time.time() - t0:.1f}s")

    # --- redaction
    t = await alice.call("ask", conversation_id=cid, question="PLANT please")
    a = await until_done(alice, t["ticket"])
    ans = a.get("answer", "")
    check(PLANTED not in ans and PG_PASS not in ans and ans.count("[redacted]") == 2, "planted secrets -> [redacted]", ans)

    # --- errors, close
    t = await alice.call("ask", conversation_id=cid, question="FAIL now")
    a = await until_done(alice, t["ticket"])
    check(a.get("status") == "error" and "error" in a, "failed run -> status error", str(a))
    await alice.call("close_conversation", conversation_id=cid)
    r = await alice.call("ask", conversation_id=cid, question="more")
    check("_error" in r and "closed" in r["_error"], "closed conversation refuses asks")
    check(all(c["conversation_id"] != cid for c in await alice.call("list_conversations")), "closed hidden by default")

    # --- messages
    mates = await alice.call("list_teammates")
    by = {m["key"]: m for m in mates} if isinstance(mates, list) else {}
    check(by.get("alice", {}).get("you") is True and by.get("bob", {}).get("teams") == ["BIL:backend"],
          "list_teammates: you flag and teams in your projects only (not PRC)", str(mates))
    check(by.get("dan", {}).get("reachable") == {"telegram": False, "mattermost": False}
          and by["bob"]["reachable"]["telegram"] is True, "list_teammates: reachability", str(by.get("dan")))
    check("telegram_chat_id" not in json.dumps(mates) and "222" not in json.dumps(mates), "chat ids are not exposed")
    r = await carol.call("send_message", to=["bob"], text="hi")
    check("_error" in r and "people.yaml" in r["_error"], "caller not in people.yaml can't send", str(r))
    r = await alice.call("send_message", to=["Bob"], text=f"Can you check <the> route? {PLANTED}")
    check(r.get("sent") == ["Bob Builder"] and r.get("failed") == [], "message to Bob sent", str(r))
    m = TG_SENT[-1] if TG_SENT else {}
    t = m.get("text", "")
    check(str(m.get("chat_id")) == "222" and m.get("parse_mode") == "HTML", "sent to Bob's chat id as HTML", str(m))
    check(t.startswith("💬 <b>From Alice Archer</b> (via Claude)") and "@alice_tg" in t and "Reply to Alice directly" in t,
          "signed with the caller's name and their contact", t)
    check("&lt;the&gt;" in t and PLANTED not in t and "[redacted]" in t, "text is HTML-escaped and secrets redacted", t)
    n = len(TG_SENT)
    r = await alice.call("send_message", to="bobb", text="by telegram username, as a plain string")
    check(r.get("sent") == ["Bob Builder"] and len(TG_SENT) == n + 1, "a single string recipient and @username work", str(r))
    for to, want, label in ((["nobody"], "no such teammate", "unknown name refused"),
                            (["Sam"], "matches Sam One, Sam Two", "ambiguous first name refused"),
                            (["alice"], "yourself", "message to yourself refused"),
                            (["team:backend"], "which project", "team without project refused")):
        r = await alice.call("send_message", to=to, text="x")
        check("_error" in r and want in r["_error"], label, str(r))
    n = len(TG_SENT)
    r = await alice.call("send_message", to=["bob", "dan"], text="x")
    check("_error" in r and "Dan Dark" in r["_error"] and len(TG_SENT) == n, "an unreachable recipient → nothing sent", str(r))
    r = await alice.call("send_message", to=["bob"], text="x", channel="mattermost")
    check("_error" in r and "not set up" in r["_error"], "Mattermost not offered yet", str(r))
    r = await alice.call("send_message", to=["bob"], text="y" * 3501)
    check("_error" in r and "3500" in r["_error"], "text over 3500 characters refused", str(r))
    r = await alice.call("send_message", to=["team:design"], text="design question", project="BIL")
    check(sorted(r.get("sent", [])) == ["Sam One", "Sam Two"] and "design team (BIL)" in TG_SENT[-1]["text"],
          "team:design in BIL → both designers, named in the header", str(r))
    r = await alice.call("send_message", to=["team:backend"], text="x", project="PRC")
    check("_error" in r and "not a member" in r["_error"], "team in a project you're not in refused", str(r))
    r = await alice.call("send_message", to=["team:data"], text="x", project="BIL")
    check("_error" in r and "no recipients" in r["_error"], "a team of only you → nobody to message", str(r))
    r = await alice.call("send_message", to=["zed", "bob"], text="partial")
    check(r.get("sent") == ["Bob Builder"] and r.get("failed", [{}])[0].get("name") == "Zed Broken"
          and "chat not found" in r["failed"][0]["error"], "a Telegram error is reported per recipient", str(r))
    r = await alice.call("send_message", to=["bob", "sam1"], text="over the limit")  # 5 sent so far, limit 6
    check("_error" in r and "per hour" in r["_error"], "hourly limit counts recipients; nothing sent", str(r))
    r = await bob.call("send_message", to=["alice"], text="from bob")
    check(r.get("sent") == ["Alice Archer"], "the limit is per sender", str(r))
    st = app_state()["store"]
    cols = [x[1] for x in st.q("pragma table_info(messages)")]
    rows = st.q("select sender, recipient, status from messages order by created_at")
    check("text" not in cols and [tuple(x) for x in rows][:2] == [("alice", "bob", "sent")] * 2, "message log keeps who/whom/status, no text", str(cols))

    # --- action journal: POST /journal from the hook, then blame / revert_plan over MCP
    J_URL = f"http://127.0.0.1:{server_port}/journal"
    hook = {"tool_name": "mcp__plugin_core_plane__workitem", "tool_use_id": "tu-1", "session_id": "s-alice",
            "tool_input": {"action": "update", "project_id": BIL, "workitem_id": "wi-1", "state": "st-2"},
            "tool_response": {"content": [{"type": "text", "text": "{}"}]}}
    async with httpx.AsyncClient() as hc:
        r = await hc.post(J_URL, json=hook, headers={"X-Journal-Phase": "post"})
        check(r.status_code == 401, "journal: no token -> 401", str(r.status_code))
        r = await hc.post(J_URL, json=hook, headers={"Authorization": "Bearer tok-alice-0001", "X-Journal-Phase": "post",
                                                    "X-Agent-Role": "data", "X-Agent-Name": "triage"})
        ev = r.json().get("event")
        check(r.status_code == 200 and ev, "journal: a person's write is recorded", r.text)
        read = {**hook, "tool_input": {"action": "list", "project_id": BIL}}
        r = await hc.post(J_URL, json=read, headers={"Authorization": "Bearer tok-bob-000002", "X-Journal-Phase": "post"})
        check(r.status_code == 200 and r.json().get("event") is None, "journal: reads are not recorded", r.text)
        r = await hc.post(J_URL + "/batch", headers={"Authorization": "Bearer tok-carol-0003"},
                          content=json.dumps({"payload": {**hook, "tool_use_id": "tu-2"}, "agent": "nightly", "run": "n-1"}) + "\n")
        check(r.status_code == 200 and r.json().get("ingested") == 1, "journal: spooled batch accepted", r.text)
    jr = app_state()["journal"]
    e = jr.events(event=ev)[0]
    check(e["person"] == "alice" and e["actor_kind"] == "person-agent" and e["agent"] is None and e["role"] == "data",
          "journal: a person in people.yaml is that person's agent, whatever X-Agent-Name says", str(e))
    bot = jr.events(run="n-1")
    check(bot and bot[0]["actor_kind"] == "service-agent" and bot[0]["agent"] == "nightly",
          "journal: a token that isn't a person is a service agent", str(bot))
    rows = await alice.call("blame", target="wi-1")
    check(isinstance(rows, list) and any("alice's agent" in x["how"] for x in rows), "blame over MCP", str(rows)[:300])
    plan = await alice.call("revert_plan", session="s-alice")
    check(plan.get("plan", "").startswith("rv-") and "revert-apply" in plan.get("apply", ""),
          "revert_plan over MCP: a dry-run plan with the apply command", str(plan)[:300])
    rows = await carol.call("blame", target="wi-1")
    check(isinstance(rows, list) and not any("alice's agent" in x["how"] for x in rows),
          "blame: changes in projects you aren't a member of are hidden", str(rows)[:300])

    # --- every answer leaves a claim count (no text) for the Agent Ops Reference's quality report
    qf = TMP / "data" / "answers-quality.jsonl"
    rows = [json.loads(x) for x in qf.read_text().splitlines()] if qf.exists() else []
    check(rows and all(set(r) == {"ts", "project", "depth", "claims", "unsourced", "rate", "sources"} for r in rows)
          and rows[0]["project"] == "BIL", "answers: claim counts recorded per answer, no text", str(rows[:2]))

    # --- lineage: where a table's data goes, and what a page is built from
    r = await alice.call("lineage", node="ledger")
    check(r.get("matched") == ["table:analytics.ledger"] and "/revenue (Revenue dashboard)" in r["summary"].get("page", [])
          and r.get("owners") == ["kenji", "noor"], "lineage: impact of a table reaches the page, with owners", str(r)[:300])
    r = await alice.call("lineage", node="/revenue", direction="upstream")
    check("analytics.ledger" in r.get("summary", {}).get("table", []), "lineage: upstream of a page reaches the warehouse", str(r)[:300])

    # --- logs carry timings only, never question or answer text
    logged = "\n".join(LOGGED)
    check("ticket=" in logged and "seconds=" in logged, "runs are logged with timings", logged[-300:])
    check(not any(w in logged for w in ("first question", "follow-up", "PLANT", PLANTED, "SLOW", "fake/path")),
          "no question/answer content in logs")
    check("message id=" in logged and not any(w in logged for w in ("design question", "from bob", "route?", TG_TOKEN)),
          "messages are logged without their text or the bot token")


def unit() -> None:
    red = server.Redactor(str(TMP / "env"), [str(TMP / "*.token")])
    out = red(f"a {PLANTED} b tracker-token-file-value-99 c abc postgresql://app_ro:{PG_PASS}@db.example.com:5432/app")
    check(PLANTED not in out and "tracker-token-file-value-99" not in out and PG_PASS not in out and " abc " in out,
          "redactor: env values, token files, URL parts; short values kept", out)
    # Shapes the env file doesn't know (built in pieces so this file holds no token-shaped text).
    gl, jwt = "glpat-" + "Ab3dE6gH9jK2mN5pQ8sT", "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "zdWIiOiIxMjM0NTYifQ" + ".s1gnatureXYZ123abc"
    out = red(f"t={gl} j={jwt} curl -H 'X-API-Key: {'Zx9Qw2Er7Ty4Ui' + '1Op6As3Df'}' https://bob:{'hunter2' + 'secret'}@h/x")
    check(gl not in out and jwt not in out and "Zx9Qw2Er7Ty4Ui1Op6As3Df" not in out and "hunter2secret" not in out
          and "X-API-Key: [redacted]" in out and "https://bob:[redacted]@h/x" in out,
          "redactor: token shapes (GitLab, JWT, auth header, URL password)", out)
    body, src = server.split_sources("Answer.\n\n## Sources\n- a.ts:1\n- b.ts:2\n")
    check(body == "Answer." and src == ["a.ts:1", "b.ts:2"], "split_sources")


class FakeServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without HTTPServer's reverse-DNS lookup of the bind address
    (socket.getfqdn), which can take ~35 s, e.g. on GitHub's macOS runners."""
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def main() -> int:
    tg = FakeServer(("127.0.0.1", tg_port), FakeTelegram)
    threading.Thread(target=tg.serve_forever, daemon=True).start()
    connector = server.J.Connector({}, tracker_factory=FakeTracker, codehost_factory=lambda k, t: None, hosts={})
    app = server.build_app(server.Config.from_env(), connector=connector)
    APP.append(app)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=server_port, log_level="warning"))

    async def run():
        task = asyncio.create_task(srv.serve())
        while not srv.started:
            await asyncio.sleep(0.05)
        try:
            unit()
            await tests()
        finally:
            srv.should_exit = True
            await task

    asyncio.run(run())
    tg.shutdown()
    print(f"\n{'all passed' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
