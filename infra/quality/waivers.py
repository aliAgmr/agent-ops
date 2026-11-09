#!/usr/bin/env python3
"""Waivers: a person's documented decision that one failing check or finding on one MR may pass.

The release agent reads `waive <id>: <reason>` comments on an MR and records each one here; this
script, not the agent, decides whether a waiver counts, so the rules can't be bent:

- the author is a person (not a service account: people.yaml `service`, AGENT_BOT_USERNAMES; when
  people.yaml lists code-host usernames (`code_host_usernames`), it must be one of them);
- the reason is specific: at least 20 characters and 4 words, not boilerplate ("false positive",
  "not a bug", "known issue" …), and not copied word for word from a waiver on another MR;
- at most 3 waivers per MR ("too many waivers: fix instead").

  waivers add --mr <project>!<iid> --id <check-or-finding-id> --author <username> --reason "<text>"
  waivers list --mr <project>!<iid>          the accepted waivers of one MR
  waivers repeated [--days 30] [--min 3]     ids waived on --min or more MRs: the check, not the
                                             code, probably needs changing (nightly reports them)
Prints JSON. The ledger is AGENT_WAIVERS_FILE (default ~/.local/share/agent-ops/waivers.jsonl).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
LEDGER = Path(os.environ.get("AGENT_WAIVERS_FILE", "~/.local/share/agent-ops/waivers.jsonl")).expanduser()
MAX_PER_MR = 3
MIN_CHARS, MIN_WORDS = 20, 4
BOILERPLATE = {"false positive", "not a bug", "known issue", "known bug", "intended", "by design",
               "wont fix", "won't fix", "ok", "fine", "not relevant", "n/a", "na", "approved", "lgtm",
               "will fix later", "fix later", "temporary", "as discussed"}
MR_RE = re.compile(r"^[A-Za-z0-9._/-]+![0-9]+$")
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", text.lower())).strip()


def records(path: Path = LEDGER) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def people_usernames() -> tuple[set, set]:
    """(code-host usernames of people, usernames/emails of service accounts) from people.yaml:
    each person's `code_host_usernames` ({code_hosts key: username}), and everything under
    `service` (the tracker bots, `code_host_bots` per host key)."""
    people, service = set(), {u.strip().lower() for u in os.environ.get("AGENT_BOT_USERNAMES", "").split(",") if u.strip()}
    p = Path(os.environ.get("AGENT_PEOPLE_FILE", str(ROOT / "people.yaml")))
    if p.is_file():
        import yaml
        data = yaml.safe_load(p.read_text()) or {}
        for v in (data.get("people") or {}).values():
            names = (v or {}).get("code_host_usernames") or {}
            for u in (names.values() if isinstance(names, dict) else [names]):
                if u:
                    people.add(str(u).lower())
        for v in (data.get("service") or {}).values():
            for u in (v.values() if isinstance(v, dict) else [v]):
                if isinstance(u, str) and u:
                    service.add(u.lower())
    return people, service


def add(mr: str, wid: str, author: str, reason: str, path: Path = LEDGER, now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    if not MR_RE.match(mr) or not ID_RE.match(wid):
        return {"accepted": False, "why": "bad --mr (project!iid) or --id"}
    author_l = author.strip().lower()
    people, service = people_usernames()
    if not author_l or author_l in service or author_l.endswith("-bot") or "bot" == author_l:
        return {"accepted": False, "why": f"{author or 'no author'} is a service account: only a person can waive"}
    if people and author_l not in people:
        return {"accepted": False, "why": f"{author} is not a person in people.yaml"}
    r = norm(reason)
    if len(reason.strip()) < MIN_CHARS or len(r.split()) < MIN_WORDS:
        return {"accepted": False, "why": f"the reason must say why, specifically ({MIN_CHARS}+ characters, {MIN_WORDS}+ words)"}
    if r in BOILERPLATE or any(r == b or r.startswith(b + " ") and len(r.split()) <= len(b.split()) + 1 for b in BOILERPLATE):
        return {"accepted": False, "why": "the reason is boilerplate: say what makes this case safe"}
    recs = records(path)
    mine = [x for x in recs if x.get("mr") == mr]
    if any(x.get("id") == wid for x in mine):
        return {"accepted": True, "why": "already recorded", "duplicate": True}
    if any(norm(x.get("reason", "")) == r and x.get("mr") != mr for x in recs):
        return {"accepted": False, "why": "the same reason was used word for word on another MR: write this case's reason"}
    if len(mine) >= MAX_PER_MR:
        return {"accepted": False, "why": f"too many waivers on this MR (max {MAX_PER_MR}): fix instead"}
    rec = {"mr": mr, "id": wid, "author": author, "reason": reason.strip()[:500], "at": int(now)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"accepted": True, "why": "recorded", "waiver": rec}


def repeated(days: int = 30, min_mrs: int = 3, path: Path = LEDGER, now: Optional[float] = None) -> List[Dict[str, Any]]:
    since = (now or time.time()) - days * 86400
    by_id: Dict[str, set] = {}
    for x in records(path):
        if x.get("at", 0) >= since:
            by_id.setdefault(x["id"], set()).add(x["mr"])
    return [{"id": k, "mrs": sorted(v)} for k, v in sorted(by_id.items()) if len(v) >= min_mrs]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="waivers", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a_ = sub.add_parser("add")
    for k in ("--mr", "--id", "--author", "--reason"):
        a_.add_argument(k, required=True)
    l_ = sub.add_parser("list"); l_.add_argument("--mr", required=True)
    r_ = sub.add_parser("repeated"); r_.add_argument("--days", type=int, default=30); r_.add_argument("--min", type=int, default=3)
    a = ap.parse_args(argv)
    if a.cmd == "add":
        out: Any = add(a.mr, a.id, a.author, a.reason)
    elif a.cmd == "list":
        out = [x for x in records() if x.get("mr") == a.mr]
    else:
        out = repeated(a.days, a.min)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
