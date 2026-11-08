#!/usr/bin/env python3
"""Cheap "is there anything to do?" check that run-agent.sh runs before starting Claude, so idle
ticks cost nothing (the agents share a plan with usage limits). Talks to the task board and the code
host through infra/adapters; no model, no writes.

  precheck.py <triage|analyst|release|nightly|jenkins-watch> <since-iso8601>

Exit 0 = work to do (prints "work: <board>: <why>"), 1 = idle, 3 = the check itself failed (a
missing setting or an API error; run-agent.sh then runs Claude anyway: a broken check must never
stop the agents). nightly is always work: nightly.sh schedules it. jenkins-watch is
infra/jenkins-watch/watch.py collect (Jenkins through its MCP server, read-only; same exit codes).

The conditions mirror each agent's tick.md; keep them in sync when a tick changes.
Boards: TRIAGE_PROJECTS (space-separated keys), else every key in projects.yaml.
PRECHECK_RULES_SINCE (optional ISO time): state changes before it never count for the review rules
(set it once when you roll the rules out on a board with history).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adapters as A  # noqa: E402

OPEN_STATES = ["Triaged", "Todo", "In Progress", "In Review", "Ready for E2E", "Blocked", "Backlog"]
SKIP_LABELS = {"lesson", "directive", "question"}


class Done(Exception):
    def __init__(self, code: int, text: str):
        super().__init__(text)
        self.code, self.text = code, text


def work(board: Optional[str], why: str):
    raise Done(0, f"work: {board + ': ' if board else ''}{why}")


def fail(why: str):
    raise Done(3, f"precheck failed: {why}")


def need(env: Dict[str, str], *names: str) -> None:
    for n in names:
        if not env.get(n):
            fail(f"{n} is not set")


class Board:
    """One project on the task board: its states, labels and items, and the people rules."""

    def __init__(self, t: A.TrackerAdapter, pid: str, key: str, since: str, people: "People"):
        self.t, self.pid, self.key, self.since, self.people = t, pid, key, since, people
        try:
            self.state_name = {s["id"]: s["name"] for s in t.states(pid)}
            self.labels = {l["name"]: l["id"] for l in t.labels(pid)}
            self.items = t.list_items(pid)
        except A.AdapterError as e:
            fail(f"listing work items on {key} ({e})")

    def name(self, item: Dict[str, Any]) -> Optional[str]:
        return self.state_name.get(item.get("state"))

    def has(self, item: Dict[str, Any], label: str) -> bool:
        lid = self.labels.get(label)
        return bool(lid) and lid in (item.get("label_ids") or [])

    def acts(self, item_id: str) -> List[Dict[str, Any]]:
        try:
            return self.t.activities(self.pid, item_id)
        except A.AdapterError as e:
            fail(f"listing activities ({e})")
        return []

    def comments(self, item_id: str) -> List[Dict[str, Any]]:
        try:
            return self.t.list_comments(self.pid, item_id)
        except A.AdapterError as e:
            fail(f"listing comments ({e})")
        return []


class People:
    """Task-board user ids of the approvers (routing.yaml, a project may override) and of the
    service agents' bot users (people.yaml `service.tracker_*_bot`)."""

    def __init__(self, t: A.TrackerAdapter):
        self.t = t
        self._members: Optional[List[Dict[str, Any]]] = None
        self.routing = A.load_routing()
        self.people = A.load_people()

    def _ids(self, emails: Set[str]) -> Set[str]:
        if self._members is None:
            try:
                self._members = self.t.members()
            except A.AdapterError as e:
                fail(f"listing members ({e})")
        return {m["id"] for m in self._members if m.get("email") and m["email"] in emails}

    def bots(self):
        s = (self.people or {}).get("service") or {}
        rel = str(s.get("tracker_release_bot") or "").strip().lower()
        tri = str(s.get("tracker_triage_bot") or "").strip().lower()
        ngt = str(s.get("tracker_nightly_bot") or "").strip().lower()
        if not rel or not tri:
            fail("no tracker_release_bot/tracker_triage_bot in people.yaml")
        return self._ids({rel, ngt} - {""}), self._ids({tri})

    def approvers(self, board: str) -> Set[str]:
        r = self.routing
        names = (((r.get("projects") or {}).get(board) or {}).get("approvers")) or r.get("approvers") or []
        ppl = (self.people or {}).get("people") or {}
        emails = {str((ppl.get(n) or {}).get("email") or "").strip().lower() for n in names} - {""}
        if not emails:
            fail("no approver emails in routing.yaml/people.yaml")
        return self._ids(emails)


def check_board(b: Board, agent: str) -> None:
    since, key = b.since, b.key
    inbox = [i for i in b.items if b.name(i) == "Inbox"]
    queued = [i for i in inbox if b.has(i, "analysis:needed")]
    if agent == "analyst":
        if queued:
            work(key, f"{len(queued)} Inbox item(s) waiting for analysis")
        return
    # tick step 1: anything in Inbox that isn't waiting for the analyst
    n = len(inbox) - len(queued)
    if n:
        work(key, f"{n} item(s) in Inbox")
    # tick step 2b: Backlog items imported from elsewhere that still need their review
    if "backlog:to-review" in b.labels:
        n = sum(1 for i in b.items if b.name(i) == "Backlog" and b.has(i, "backlog:to-review"))
        if n:
            work(key, f"{n} backlog item(s) to review")
    # tick steps 2e/2f: effort labels, only on boards that have them
    effort = {lid for name, lid in b.labels.items() if name.startswith("effort:")}
    open_items: Dict[str, Dict[str, Any]] = {}
    if effort:
        skip = {b.labels[n] for n in SKIP_LABELS if n in b.labels}
        for i in b.items:
            ids = set(i.get("label_ids") or [])
            if b.name(i) not in OPEN_STATES or ids & skip:
                continue
            if b.name(i) == "Backlog" and b.has(i, "backlog:to-review"):
                continue
            open_items[i["id"]] = {"item": i, "has": bool(ids & effort)}
        for iid, o in open_items.items():
            if o["has"]:
                continue
            _, triage_bot = b.people.bots()
            changes = [a for a in b.acts(iid) if a.get("field") == "labels" and
                       (str(a.get("old_value") or "").startswith("effort:") or str(a.get("new_value") or "").startswith("effort:"))]
            last = sorted(changes, key=lambda a: a.get("created_at") or "")[-1] if changes else None
            if last is None or last.get("actor") in triage_bot:
                work(key, "open item(s) without an effort label")
    # tick step 6: review/Done rules. Forbidden: → Done by anyone but an approver or the release/
    # nightly bot; In Review → Ready for E2E or → Cancelled by a non-approver. The triage bot itself
    # is exempt (it closes question items and makes the reverts).
    rules_since = os.environ.get("PRECHECK_RULES_SINCE", "")
    eff = max(since, rules_since) if rules_since else since
    moved = [i for i in b.items if (i.get("updated_at") or i.get("created_at") or "") > eff]
    if moved:
        approvers = b.people.approvers(key)
        release_bot, triage_bot = b.people.bots()
        bots = release_bot | triage_bot
        for i in moved:
            acts = b.acts(i["id"])
            bad = unknown = 0
            for a in acts:
                ts = a.get("created_at") or ""
                if a.get("field") != "state" or ts <= since or (rules_since and ts < rules_since):
                    continue
                actor = a.get("actor")
                if actor and actor in triage_bot:
                    continue
                old = b.state_name.get(a.get("old_identifier")) or a.get("old_value") or ""
                new = b.state_name.get(a.get("new_identifier")) or a.get("new_value") or ""
                if new == "Done":
                    hit = actor is None or actor not in (approvers | release_bot)
                elif new == "Cancelled" or (old == "In Review" and new == "Ready for E2E"):
                    hit = actor is None or actor not in approvers
                else:
                    hit = False
                if hit:
                    if actor is None:
                        unknown += 1
                    else:
                        bad += 1
            if bad:
                work(key, "forbidden state change (Done/Ready for E2E/Cancelled) by a non-approver")
            if unknown:
                work(key, "Done/Ready for E2E/Cancelled set by an unknown user")
            # tick step 2e: a person changed an open item's scope since the last run
            if not effort or i["id"] not in open_items:
                continue
            if any(a.get("field") in ("name", "description", "parent") and (a.get("created_at") or "") > since
                   and (a.get("actor") is None or a.get("actor") not in bots) for a in acts):
                work(key, "title, description or children changed (effort re-assessment)")
            for c in b.comments(i["id"]):
                who = c.get("actor") or c.get("created_by")
                if (c.get("created_at") or "") > since and (who is None or who not in bots):
                    work(key, "new comment (effort re-assessment)")
    # tick step 5: new comments on open question items
    for i in b.items:
        if b.has(i, "question") and b.name(i) not in ("Done", "Cancelled"):
            if any((c.get("updated_at") or c.get("created_at") or "") > since for c in b.comments(i["id"])):
                work(key, "new comment on a question item")


def boards() -> List[str]:
    if os.environ.get("TRIAGE_PROJECTS"):
        return os.environ["TRIAGE_PROJECTS"].split()
    return list(A.load_projects().keys()) or ["BIL"]


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("usage: precheck.py <triage|analyst|release|nightly|jenkins-watch> <since>", file=sys.stderr)
        return 3
    agent, since = argv[0], argv[1]
    env = A.env_values()
    try:
        if agent in ("triage", "analyst"):
            need(env, "TRACKER_BASE_URL", "TRACKER_WORKSPACE", "TRACKER_TRIAGE_TOKEN")
            t = A.tracker(role="triage", env=env)
            try:
                projects = t.projects()
            except A.AdapterError as e:
                fail(f"listing projects ({e})")
            people = People(t)
            for key in boards():
                pid = next((p["id"] for p in projects if p.get("identifier") == key), None)
                if not pid:
                    fail(f"project {key} not found (is the triage bot a member?)")
                check_board(Board(t, pid, key, since, people), agent)
            raise Done(1, "idle")
        if agent == "release":
            routing = A.load_routing()
            repos = A.all_repos(routing, release_only=True)
            for r in repos:
                var = f"CODEHOST_{A.codehost_key(r.get('host') or 'main')}_TOKEN"
                need(env, var)
            for r in repos:
                g = A.codehost(routing, r.get("host") or "main", env=env)
                try:
                    mrs = g.list_merge_requests(r["path"], state="all", target_branch="main", updated_after=since)
                except A.AdapterError as e:
                    fail(f"listing merge requests in {r['path']} ({e})")
                if mrs:
                    work(None, f"{len(mrs)} MR(s) into main changed in {r['path']}")
            raise Done(1, "idle")
        if agent == "nightly":
            work(None, "nightly pass (scheduled by nightly.sh; never skipped)")
        if agent == "jenkins-watch":
            # Reads every job and the watched topic's new alerts, keeps the incident state and writes
            # facts.json for the agent; prints its own work/idle/failed line.
            watch = Path(__file__).resolve().parents[1] / "jenkins-watch" / "watch.py"
            rc = subprocess.run([sys.executable, "-I", str(watch), "collect"], timeout=600).returncode
            return rc if rc in (0, 1) else 3
        fail(f"unknown agent {agent}")
    except Done as d:
        print(d.text, file=sys.stderr if d.code == 3 else sys.stdout)
        return d.code
    except Exception as e:  # noqa: BLE001 - a broken check fails open (exit 3), never idle
        print(f"precheck failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
