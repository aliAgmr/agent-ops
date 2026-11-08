#!/usr/bin/env python3
"""Reads the team bot's Telegram updates (getUpdates, outbound only: the agent host needn't be
reachable from the internet, so no webhook) and keeps the messages of the watched group topic for
the Jenkins watcher (infra/jenkins-watch), so it can reply to the alerts other bots post there.

Telegram delivers another bot's group messages to ours only with Bot-to-Bot Communication Mode on
(@BotFather) and our bot an admin of the group (or privacy mode off).

  poll.py            fetch new updates once (launchd <prefix>.telegram-poll or the systemd
                     telegram-poll.timer runs it every minute)
  poll.py chats      the chats and topics seen so far (ids and titles, never message text): use it to
                     find TELEGRAM_WATCH_CHAT_ID / TELEGRAM_WATCH_TOPIC_ID after adding the bot
  poll.py recent [N] the last N stored messages of the watched topic (for checks)
  poll.py private    people who opened a private chat with the bot (id, username, name; no text)

Only the watched chat's messages are stored (text included); every other update is acknowledged
and dropped. Kept 30 days. The poller consumes the bot's updates, so it also records who opens a
private chat with the bot (id, username, name; never the text): `poll.py private`, which
infra/n8n/telegram-chat-ids.sh reads too.
Settings: TELEGRAM_BOT_TOKEN, TELEGRAM_WATCH_CHAT_ID (env file). Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adapters as A  # noqa: E402  (the env file reader)

DB = Path(os.environ.get("AGENT_TELEGRAM_DB", "~/.local/share/agent-ops/telegram/messages.sqlite3")).expanduser()
API = os.environ.get("TELEGRAM_API", "https://api.telegram.org").rstrip("/")
KEEP_DAYS = 30
SCHEMA = """
create table if not exists messages (
  chat_id integer not null, message_id integer not null, thread_id integer,
  date integer not null, from_id integer, from_username text, from_is_bot integer not null default 0,
  text text, reply_to integer,
  primary key (chat_id, message_id)
);
create index if not exists messages_date on messages(chat_id, thread_id, date);
create table if not exists chats (
  chat_id integer not null, thread_id integer not null default 0, type text, title text,
  last_seen integer not null, bot_senders text,
  primary key (chat_id, thread_id)
);
create table if not exists private_chats (
  chat_id integer primary key, username text, first_name text, last_name text, last_seen integer not null
);
create table if not exists state (key text primary key, value text);
"""


def db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    try:
        os.chmod(DB, 0o600)
    except OSError:
        pass
    return c


def call(token: str, method: str, params: Dict[str, Any], timeout: int = 30) -> Any:
    req = urllib.request.Request(f"{API}/bot{token}/{method}", data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (Telegram)
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Telegram {method}: HTTP {e.code}") from None
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method}: {body.get('description', 'not ok')}")
    return body["result"]


def store(c: sqlite3.Connection, updates: List[Dict[str, Any]], watch_chat: Optional[int]) -> int:
    kept = 0
    now = int(time.time())
    for u in updates:
        m = u.get("message") or u.get("edited_message") or u.get("channel_post")
        if not isinstance(m, dict):
            continue
        chat = m.get("chat") or {}
        cid, thread = chat.get("id"), m.get("message_thread_id") or 0
        frm = m.get("from") or {}
        if chat.get("type") in ("group", "supergroup", "channel"):
            row = c.execute("select bot_senders from chats where chat_id=? and thread_id=?", (cid, thread)).fetchone()
            bots = set(json.loads(row["bot_senders"])) if row and row["bot_senders"] else set()
            if frm.get("is_bot") and frm.get("username"):
                bots.add(frm["username"])
            c.execute("insert or replace into chats (chat_id, thread_id, type, title, last_seen, bot_senders) values (?,?,?,?,?,?)",
                      (cid, thread, chat.get("type"), chat.get("title"), now, json.dumps(sorted(bots))))
        if chat.get("type") == "private":   # someone pressed Start: keep who (for DMs), never the text
            c.execute("insert or replace into private_chats (chat_id, username, first_name, last_name, last_seen) values (?,?,?,?,?)",
                      (cid, chat.get("username") or frm.get("username") or "", chat.get("first_name") or frm.get("first_name") or "",
                       chat.get("last_name") or frm.get("last_name") or "", m.get("date") or now))
        if watch_chat is None or cid != watch_chat:
            continue
        reply = (m.get("reply_to_message") or {}).get("message_id")
        c.execute("""insert or replace into messages (chat_id, message_id, thread_id, date, from_id, from_username,
                     from_is_bot, text, reply_to) values (?,?,?,?,?,?,?,?,?)""",
                  (cid, m.get("message_id"), thread or None, m.get("date") or now, frm.get("id"), frm.get("username"),
                   1 if frm.get("is_bot") else 0, (m.get("text") or m.get("caption") or "")[:8000], reply))
        kept += 1
    c.execute("delete from messages where date < ?", (now - KEEP_DAYS * 86400,))
    c.commit()
    return kept


def poll(env: Dict[str, str]) -> Dict[str, Any]:
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set")
    watch = env.get("TELEGRAM_WATCH_CHAT_ID")
    watch_chat = int(watch) if watch and re.match(r"^-?\d+$", watch) else None
    c = db()
    row = c.execute("select value from state where key='offset'").fetchone()
    offset = int(row["value"]) if row else 0
    total = kept = 0
    for _ in range(10):  # at most 1000 updates per run
        ups = call(token, "getUpdates", {"offset": offset, "timeout": 0, "limit": 100,
                                         "allowed_updates": ["message", "edited_message", "channel_post"]})
        if not ups:
            break
        kept += store(c, ups, watch_chat)
        total += len(ups)
        offset = max(u["update_id"] for u in ups) + 1
        c.execute("insert or replace into state (key, value) values ('offset', ?)", (str(offset),))
        c.commit()
        if len(ups) < 100:
            break
    return {"updates": total, "stored": kept}


def main(argv: List[str]) -> int:
    env = A.env_values()
    cmd = argv[1] if len(argv) > 1 else "poll"
    if cmd == "poll":
        print(json.dumps(poll(env)))
    elif cmd == "chats":
        rows = db().execute("select chat_id, thread_id, type, title, last_seen, bot_senders from chats order by last_seen desc").fetchall()
        print(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False))
    elif cmd == "private":
        rows = db().execute("select chat_id, username, first_name, last_name, last_seen as last from private_chats").fetchall()
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
    elif cmd == "recent":
        n = int(argv[2]) if len(argv) > 2 else 10
        rows = db().execute("select message_id, thread_id, date, from_username, from_is_bot, substr(text,1,200) text "
                            "from messages order by date desc limit ?", (n,)).fetchall()
        print(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
