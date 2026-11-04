# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==1.30.0"]
# ///
"""Live test of a running agent-mcp server with a real task-board token and real Claude runs.

  uv run --script tests/agent-mcp/test_live.py [local-url] [public-url]
  token: AGENT_MCP_TEST_TOKEN, else ~/.config/agent-ops/tracker-admin.token (never printed)
  public URL: the second argument, else AGENT_MCP_PUBLIC_URL + /mcp (skipped when neither is set)

Asks a small real question, then a follow-up that only makes sense with the first answer's
context, then repeats calls through the public URL.
  AGENT_MCP_TEST_PROJECT  project key to ask about (default: the first one list_projects returns)
  AGENT_MCP_TEST_SYMBOL   a function name that exists in that project's code (required)"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

LOCAL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/mcp"
PUBLIC = sys.argv[2] if len(sys.argv) > 2 else (os.environ.get("AGENT_MCP_PUBLIC_URL", "").rstrip("/") + "/mcp" if os.environ.get("AGENT_MCP_PUBLIC_URL") else "")
TOKEN = os.environ.get("AGENT_MCP_TEST_TOKEN") or Path("~/.config/agent-ops/tracker-admin.token").expanduser().read_text().strip()
PROJECT = os.environ.get("AGENT_MCP_TEST_PROJECT", "")
SYMBOL = os.environ.get("AGENT_MCP_TEST_SYMBOL", "")
fails: list[str] = []


def check(cond: bool, label: str, detail: str = "") -> None:
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  [{detail[:500]}]"), flush=True)
    if not cond:
        fails.append(label)


async def call(url: str, tool: str, **args):
    async with streamablehttp_client(url, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30,
                                     sse_read_timeout=120) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            text = res.content[0].text if res.content else ""
            if res.isError:
                return {"_error": text}
            sc = res.structuredContent
            if sc is not None:
                return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
            return json.loads(text)


async def answer(url: str, ticket: str) -> dict:
    t0 = time.time()
    while True:
        a = await call(url, "get_answer", ticket=ticket, wait_seconds=50)
        if a.get("status") in ("done", "error") or "_error" in a or time.time() - t0 > 1500:
            a["_seconds"] = round(time.time() - t0)
            return a


async def main() -> int:
    if not SYMBOL:
        raise SystemExit("set AGENT_MCP_TEST_SYMBOL to a function name in the project's code")
    projects = await call(LOCAL, "list_projects")
    key = PROJECT or (projects[0]["project"] if projects else "")
    check(any(p.get("project") == key for p in projects), f"list_projects shows {key}", str(projects))
    conv = await call(LOCAL, "create_conversation", project=key, title=f"live test: {SYMBOL}")
    cid = conv["conversation_id"]
    check(bool(cid), "create_conversation", str(conv))

    t = await call(LOCAL, "ask", conversation_id=cid, depth="quick",
                   question=f"Which file defines {SYMBOL} and what does it return? Short answer.")
    a1 = await answer(LOCAL, t["ticket"])
    print(f"--- answer 1 ({a1['_seconds']} s)\n{a1.get('answer') or a1}\nsources: {a1.get('sources')}\n", flush=True)
    check(a1.get("status") == "done" and SYMBOL in (a1.get("answer") or ""), "first answer", str(a1))

    t = await call(LOCAL, "ask", conversation_id=cid, depth="quick", question="And who calls it?")
    a2 = await answer(LOCAL, t["ticket"])
    print(f"--- answer 2 ({a2['_seconds']} s)\n{a2.get('answer') or a2}\nsources: {a2.get('sources')}\n", flush=True)
    text2 = a2.get("answer") or ""
    check(a2.get("status") == "done", "follow-up answered", str(a2))
    # Context carried over: the follow-up never names the function, so the answer must.
    check(SYMBOL in text2, f"follow-up used the first question's context (names {SYMBOL})", text2)

    if PUBLIC:
        pub = await call(PUBLIC, "list_projects")
        check(any(p.get("project") == key for p in pub), f"public URL: list_projects shows {key}", str(pub))
        convs = await call(PUBLIC, "list_conversations", project=key)
        check(any(c["conversation_id"] == cid for c in convs), "public URL: sees the same conversation", str(convs))
        pa = await call(PUBLIC, "get_answer", ticket=t["ticket"], wait_seconds=1)
        check(pa.get("status") == "done", "public URL: get_answer returns the stored answer", str(pa))
    else:
        print("public URL not set: skipped the public checks")
    await call(LOCAL, "close_conversation", conversation_id=cid)
    print("\nall passed" if not fails else f"\n{len(fails)} FAILED: {', '.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
