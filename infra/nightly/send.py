# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml==6.0.3", "requests==2.34.2"]
# ///
"""Sends a nightly run's messages (README.md here), once: each project's summary to its topic in the
team's Telegram group (or to one person's DM), and the full list to one person's private chat
(checks.yaml `send`: `dm_to` is a people.yaml key or `question_owner`). PAGE_LINK in the texts
becomes the report page's URL (RUN_DIR/page.json). Writes RUN_DIR/sent.json; a second run for the
same folder sends nothing.

  uv run --script send.py RUN_DIR [--dry-run]
"""
import json
import os
import sys
import time

import requests
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
LIMIT = 3900


def send_config(path=None):
    """checks.yaml `send` → (topics {project: thread id}, dms {project: people key}, dm_to)."""
    cfg = (yaml.safe_load(open(path or os.environ.get("NIGHTLY_CHECKS", os.path.join(HERE, "checks.yaml")))) or {}).get("send") or {}
    topics, dms = {}, {}
    for code, dest in (cfg.get("projects") or {}).items():
        dest = dest or {}
        if dest.get("topic"):
            topics[str(code).upper()] = int(dest["topic"])
        elif dest.get("dm"):
            dms[str(code).upper()] = str(dest["dm"])
    return topics, dms, str(cfg.get("dm_to") or "question_owner")


def chunks(text):
    """Telegram allows 4096 characters: split on blank lines, then lines."""
    out, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > LIMIT and cur:
            out.append(cur.rstrip())
            cur = ""
        cur += para[:LIMIT] + "\n"
    if cur.strip():
        out.append(cur.rstrip())
    return out


def link_line(page):
    if page.get("url"):
        return page["url"]
    return f"(report page not published: {page.get('error', 'unknown error')[:120]})"


def main():
    run_dir = sys.argv[1]
    dry = "--dry-run" in sys.argv
    sent_file = os.path.join(run_dir, "sent.json")
    if os.path.exists(sent_file) and not dry:
        print(json.dumps({"skipped": "already sent"}))
        return
    try:
        people = yaml.safe_load(open(os.path.join(ROOT, "people.yaml"))) or {}
    except OSError:
        people = {}
    try:
        routing = yaml.safe_load(open(os.path.join(ROOT, "routing.yaml"))) or {}
    except OSError:
        routing = {}
    topics, dms, dm_to = send_config()
    if dm_to == "question_owner":
        dm_to = str(routing.get("question_owner") or "")
    group = str((people.get("service") or {}).get("telegram_group_chat_id") or "")
    chat = lambda key: str(((people.get("people") or {}).get(key) or {}).get("telegram_chat_id") or "")
    try:
        page = json.load(open(os.path.join(run_dir, "page.json")))
    except (OSError, ValueError):
        page = {"error": "page.json missing"}
    link = link_line(page)

    msgs = []
    targets = [(code, {"to": "group", "chat_id": group, "thread": topic}) for code, topic in topics.items()]
    targets += [(code, {"to": who, "chat_id": chat(who)}) for code, who in dms.items()]
    for code, dest in targets:
        try:  # group-<KEY>.en.txt: the agent writes English summaries (other files are ignored)
            t = open(os.path.join(run_dir, f"group-{code}.en.txt"), encoding="utf-8").read().strip()
        except OSError:
            continue
        if t and t != "-":
            msgs += [{**dest, "text": c} for c in chunks(t.replace("PAGE_LINK", link))]
    try:
        dm = open(os.path.join(run_dir, "dm.en.txt"), encoding="utf-8").read().strip()
        msgs += [{"to": dm_to, "chat_id": chat(dm_to), "text": c} for c in chunks(dm.replace("PAGE_LINK", link))]
    except OSError:
        pass
    if dry:
        print(json.dumps(msgs, ensure_ascii=False, indent=1))
        return
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    results = []
    for m in msgs:
        if not m["chat_id"]:
            results.append({"to": m["to"], "ok": False, "error": "no chat id"})
            continue
        body = {"chat_id": m["chat_id"], "text": m["text"], "disable_web_page_preview": True}
        if m.get("thread"):
            body["message_thread_id"] = m["thread"]
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body, timeout=30)
        results.append({"to": m["to"], "thread": m.get("thread"), "ok": r.ok, **({} if r.ok else {"error": r.text[:200]})})
        time.sleep(1)
    json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": results}, open(sent_file, "w"))
    print(json.dumps(results))
    if not all(x["ok"] for x in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
