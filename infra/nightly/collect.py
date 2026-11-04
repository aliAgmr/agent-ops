# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml==6.0.3", "requests==2.34.2"]
# ///
"""Facts for the nightly agent (README.md here): what changed on the task board and the code hosts
since a time.

Writes one JSON file with, per project (the default project from `routing.yaml` `teams`, plus every
key of `routing.yaml` `projects`):
  - tracker: every task updated since then (state, labels, assignees, its state/assignee history
    in the window) plus every open task (for stale checks), and the member list;
  - repos: per repo in routing.yaml (any code host: GitLab or GitHub), merge/pull requests updated
    since then, ones open 5+ days, and commits on the default branch and `main` since then, each
    with the task keys found in it.
The task board and code hosts are read through the adapters in infra/adapters (Plane, GitLab and
GitHub built in). No Claude and no writes anywhere. CI, the warehouse and BI are read by the agent
itself (BI edits too, below, when Metabase is configured).

  uv run --script collect.py --since 2026-10-30T23:00:00Z --out facts.json
Tokens (the host's env file): TRACKER_NIGHTLY_TOKEN (or TRACKER_TRIAGE_TOKEN), CODEHOST_<KEY>_TOKEN.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "infra", "journal"))
import journal as J  # noqa: E402  (journal.Connector builds the tracker / code-host adapters)

PROJECT_KEYS: list = []          # set in main() from routing.yaml / projects.yaml


def keys_in(*texts, keys=None):
    """Task keys (`BIL-12`) mentioned in any of the texts, upper-cased, deduped, in order. `keys`:
    the project keys to look for (default: the configured projects)."""
    ks = [k for k in (keys if keys is not None else PROJECT_KEYS) if k]
    if not ks:
        return []
    rx = re.compile(r"\b(" + "|".join(re.escape(k) for k in ks) + r")-(\d+)\b", re.I)
    out = []
    for t in texts:
        for m in rx.finditer(t or ""):
            k = f"{m.group(1).upper()}-{m.group(2)}"
            if k not in out:
                out.append(k)
    return out


def default_project(routing, projects_file=None):
    """The project routing.yaml `teams` describes: `default_project`, else the first project in
    projects.yaml, else "MAIN"."""
    if routing.get("default_project"):
        return str(routing["default_project"]).upper()
    try:
        data = yaml.safe_load(open(projects_file or os.path.join(ROOT, "projects.yaml"))) or {}
        keys = list((data.get("projects") or {}).keys())
        if keys:
            return str(keys[0]).upper()
    except OSError:
        pass
    return "MAIN"


def repos_by_project(routing, default="MAIN"):
    """routing.yaml → {"BIL": [repo], "PRC": [repo], ...}: the default project from `teams`, then
    every key of `projects` (a project with no repos still gets an entry, for its board);
    repo = {host, path, default_branch, only?, team}."""
    out = {default: [], **{str(code).upper(): [] for code in (routing.get("projects") or {})}}

    def add(code, team, r):
        if not r or "CHANGE-ME" in str(r.get("path", "")):
            return
        if any(x["path"] == r["path"] for x in out[code]):
            return
        out[code].append({**r, "team": team})

    for team, t in (routing.get("teams") or {}).items():
        for r in (t or {}).get("repos") or []:
            add(default, team, r)
    for code, p in (routing.get("projects") or {}).items():
        for team, t in ((p or {}).get("teams") or {}).items():
            for r in (t or {}).get("repos") or []:
                add(str(code).upper(), team, r)
    return out


def tracker_project(tr, project, since):
    """One project's tasks through the tracker adapter (infra/adapters/README.md)."""
    members = {m["id"]: {"email": m.get("email"), "name": m.get("display_name")} for m in tr.members(project["id"]) or []}
    states = sorted({s["name"] for s in tr.states(project["id"]) or []})
    items = tr.list_items(project["id"]) or []

    def brief(t):
        return {"key": t.get("key"), "id": t["id"], "title": t.get("name"), "state": t.get("state_name"),
                "group": t.get("state_group"), "labels": list(t.get("labels") or []),
                "assignees": [(members.get(a) or {}).get("email", a) for a in t.get("assignees") or []],
                "priority": t.get("priority"), "created_at": t.get("created_at"), "updated_at": t.get("updated_at"),
                "parent": t.get("parent")}

    changed, open_items = [], []
    for t in items:
        b = brief(t)
        if b["group"] not in ("completed", "cancelled"):
            open_items.append(b)
        if (t.get("updated_at") or "") >= since:
            hist = []
            for a in tr.activities(project["id"], t["id"]) or []:
                if (a.get("created_at") or "") < since or a.get("field") not in ("state", "assignees", "labels", "priority", None):
                    continue
                actor = a.get("actor")
                actor_id = actor.get("id") if isinstance(actor, dict) else actor
                hist.append({"at": a.get("created_at"), "field": a.get("field") or "created", "old": a.get("old_value"),
                             "new": a.get("new_value"), "by": (members.get(actor_id) or {}).get("email", actor_id)})
            changed.append({**b, "history": hist})
    return {"id": project["id"], "states": states,
            "members": sorted(m["email"] for m in members.values() if m.get("email")),
            "changed": changed, "open": open_items}


def repo_facts(ch, repo, since):
    """One repo's merge/pull requests and commits through the code-host adapter."""
    stale_before = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    path = repo["path"]

    def mr(m):
        return {"iid": m.get("iid"), "title": m.get("title"), "state": m.get("state"), "source": m.get("source_branch"),
                "target": m.get("target_branch"), "author": m.get("author"), "author_name": m.get("author_name"),
                "created_at": m.get("created_at"), "merged_at": m.get("merged_at"), "url": m.get("web_url"),
                "keys": keys_in(m.get("title"), m.get("description"), m.get("source_branch"))}

    mrs = [mr(m) for m in ch.list_merge_requests(path, state="all", updated_after=since) or []]
    stale = [mr(m) for m in ch.list_merge_requests(path, state="opened") or []
             if (m.get("created_at") or m.get("updated_at") or "9999") < stale_before]
    commits = {}
    for ref in dict.fromkeys([repo.get("default_branch") or "main", "main"]):
        try:
            cs = ch.commits(path, ref, since) or []
        except Exception:  # noqa: BLE001 - branch missing
            continue
        only = repo.get("only")
        commits[ref] = [{"sha": (c.get("sha") or "")[:10], "title": c.get("title"), "author": c.get("author_email"),
                         "author_name": c.get("author_name"), "at": c.get("created_at"),
                         "merge": bool(c.get("merge") or len(c.get("parent_ids") or []) > 1), "url": c.get("web_url"),
                         "keys": keys_in(c.get("title"), c.get("message"))}
                        for c in cs if not only or not c.get("paths") or any(p.startswith(only) for p in c["paths"])]
    return {"path": path, "host": repo.get("host"), "team": repo["team"], "default_branch": repo.get("default_branch"),
            "only": repo.get("only"), "merge_requests": mrs, "stale_open_mrs": stale, "commits": commits}


def metabase_edits(base, key, since, team_names):
    """Questions and dashboards created or edited since `since` by someone on the team (Metabase is
    often shared company-wide). Editors are matched by full name (people.yaml `full_name`), because
    Metabase accounts may use other emails; "<Name> API-Key" users (an agent acting for that
    person) count as that person. → [{kind, id, name, edited_at, editor, url, collection_id}]"""
    s = requests.Session()
    s.headers.update({"x-api-key": key})
    out = []
    for kind, path in (("question", "/api/card?f=all"), ("dashboard", "/api/dashboard")):
        r = s.get(base.rstrip("/") + path, timeout=60)
        r.raise_for_status()
        for x in r.json():
            e = x.get("last-edit-info") or {}
            when = e.get("timestamp") or x.get("updated_at") or ""
            name = " ".join(f"{e.get('first_name') or ''} {e.get('last_name') or ''}".lower().replace("api-key", "").split())
            who = team_names.get(name)
            if when >= since and who:
                out.append({"kind": kind, "id": x["id"], "name": x.get("name"), "edited_at": when, "editor": who,
                            "created_at": x.get("created_at"), "collection_id": x.get("collection_id"),
                            "url": f"{base.rstrip('/')}/{'question' if kind == 'question' else 'dashboard'}/{x['id']}"})
    return sorted(out, key=lambda x: x["edited_at"])


def main(argv=None, connector=None):
    global PROJECT_KEYS
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    env = {**J.env_values(os.environ.get("AGENT_OPS_ENV", "~/.config/agent-ops/env")), **{k: v for k, v in os.environ.items() if v}}
    since = a.since
    routing = yaml.safe_load(open(os.path.join(ROOT, "routing.yaml"))) or {}
    default = default_project(routing)
    by_project = repos_by_project(routing, default)
    PROJECT_KEYS = list(by_project)
    connector = connector or J.Connector(env, Path(ROOT))
    tr = connector.tracker(env.get("TRACKER_NIGHTLY_TOKEN") or env.get("TRACKER_TRIAGE_TOKEN"))
    hosts = {}
    for k in connector.hosts:
        try:
            hosts[k] = connector.codehost(k, env.get(f"CODEHOST_{J.env_key(k)}_TOKEN"))
        except Exception:  # noqa: BLE001 - reported per repo below
            hosts[k] = None

    errors = []
    out = {"since": since, "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "projects": {}, "errors": errors}
    try:
        boards = {str(p.get("identifier") or "").upper(): p for p in (tr.projects() if tr else [])}
    except Exception as e:  # noqa: BLE001
        boards = {}
        errors.append(f"tracker: {str(e)[:200]}")
    if not tr:
        errors.append("tracker: no token (TRACKER_NIGHTLY_TOKEN / TRACKER_TRIAGE_TOKEN)")
    for code, repos in by_project.items():
        entry = {"tracker": None, "repos": []}
        if code in boards:
            try:
                entry["tracker"] = tracker_project(tr, boards[code], since)
            except Exception as e:  # report, don't stop: the agent says what it couldn't read
                errors.append(f"tracker {code}: {str(e)[:200]}")
        elif tr:
            errors.append(f"tracker {code}: project not visible to the bot")
        for r in repos:
            ch = hosts.get(r.get("host"))
            if not ch:
                errors.append(f"code host {r.get('host')} {r['path']}: no adapter or token (CODEHOST_{J.env_key(str(r.get('host')))}_TOKEN)")
                continue
            try:
                entry["repos"].append(repo_facts(ch, r, since))
            except Exception as e:  # noqa: BLE001
                errors.append(f"code host {r.get('host')} {r['path']}: {str(e)[:200]}")
        out["projects"][code] = entry
    try:
        people = (yaml.safe_load(open(os.path.join(ROOT, "people.yaml"))) or {}).get("people") or {}
    except OSError:
        people = {}
    team = {" ".join(str(p.get("full_name") or "").lower().split()): p.get("email") for p in people.values() if p.get("full_name")}
    if env.get("METABASE_URL") and env.get("METABASE_AGENT_API_KEY"):
        try:
            out["metabase"] = metabase_edits(env["METABASE_URL"], env["METABASE_AGENT_API_KEY"], since, team)
        except Exception as e:  # noqa: BLE001
            errors.append(f"metabase: {str(e)[:200]}")
    with open(a.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    n = {c: (len((p["tracker"] or {}).get("changed", [])), sum(len(r["merge_requests"]) for r in p["repos"])) for c, p in out["projects"].items()}
    print(json.dumps({"out": a.out, "changed_tasks_and_mrs": n, "errors": errors}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
