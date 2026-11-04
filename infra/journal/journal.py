#!/usr/bin/env python3
"""Action journal: who (a person, their agent or a service agent) changed what, what it was
before, and how to undo it.

Every write an agent makes through a tool reaches this journal:
- people's agents and the service agents: the core plugin's `journal.sh` hook (PreToolUse and
  PostToolUse) posts the raw hook payload to agent-mcp's `/journal`, which calls `Journal.ingest`;
- scripts on the agent host (post-commit-status.sh, alert.sh, …): `journal.py record …` writes
  straight into the same database.

On `pre`, the journal saves the item as it is (a task-board item, a merge/pull request) so a revert
can put it back; on `post` it saves the item as it now is and records one event. Reads are never
stored. The task board and the code host are reached only through the adapters in
`infra/adapters/` (Plane, GitLab and GitHub built in), through `Connector` below.

Three questions it answers (CLI below, and agent-mcp tools of the same names):
- blame <target>      who changed this task / MR / PR / thing, how (by hand, a person's agent,
                      which service-agent run), when, and the transcript to read;
- suspects <term>     which recent changes (commits in the project repos, journal events) touch a
                      table, file, route or page that broke;
- revert-plan …       a dry-run plan that undoes an event, a run, a session, or everything one
                      actor did to one target; `revert-apply <plan>` carries it out, on the agent
                      host only, after the person types the plan id (never from an agent).

Standard library only (runs on the host's python3 3.9 and inside agent-mcp).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path(os.environ.get("AGENT_JOURNAL_DB", "~/.local/share/agent-mcp/journal.sqlite3")).expanduser()
MAX_TEXT = 200_000          # longest text kept per field (a work item's description)
MAX_INPUT = 20_000          # longest tool input / result kept
PENDING_TTL = 3600          # a `pre` without its `post` is dropped after an hour

SCHEMA = """
create table if not exists pending (
  tool_use_id text primary key,
  created_at real not null,
  before text
);
create table if not exists events (
  id text primary key,
  ts real not null,
  actor_kind text not null,       -- person-agent | service-agent | script | revert
  person text, user_id text, email text, role text, agent text,
  run_id text, session_id text, client text, cwd text, transcript text,
  tool text not null, action text, system text, project text,
  target_type text, target_id text, target_key text,
  summary text, input text, before text, after text, result text,
  ok integer not null default 1,
  reversible text not null,       -- auto | manual | no
  hint text,
  reverted_by text,               -- plan id that undid it
  revert_of text                  -- for revert events: the event they undo
);
create index if not exists events_target on events(target_key, ts);
create index if not exists events_target_id on events(target_id, ts);
create index if not exists events_session on events(session_id);
create index if not exists events_run on events(run_id);
create index if not exists events_ts on events(ts);
create table if not exists plans (
  id text primary key,
  created_at real not null,
  created_by text,
  selector text not null,
  steps text not null,
  status text not null,           -- planned | applied | partly-applied
  applied_at real, applied_by text, results text
);
"""

# Task-board item fields a revert can put back (the adapter item's names, infra/adapters/README.md).
ITEM_FIELDS = ("name", "state", "priority", "label_ids", "assignees", "parent", "start_date", "target_date",
               "estimate_point", "description_html")
ITEM_LIST_FIELDS = ("label_ids", "assignees")
TRACKER_READ = re.compile(r"^(list|retrieve|get|search|count|me)")
WRITE_VERB = re.compile(r"^(create|update|delete|merge|approve|play|retry|cancel|erase|resolve|stop|take_ownership|"
                        r"trigger|rebuild|replay|publish|upload|add|send|set|use_figma|generate|archive|remove|manage|"
                        r"batch|bulk|modify|post|push|edit|move|rename|close|reopen|assign|unassign|submit|request|"
                        r"fork|dismiss|lock|unlock|star|unstar|enable|disable|run|rerun)", re.I)
# Code-host MCP tools, normalised (GitLab MCP and the GitHub MCP server).
MR_CREATE = {"create_merge_request", "create_pull_request"}
MR_UPDATE = {"update_merge_request", "update_pull_request"}
ISSUE_CREATE = {"create_issue"}
ISSUE_UPDATE = {"update_issue"}
NOTE_CREATE = {"create_merge_request_note", "create_note", "create_issue_note", "create_merge_request_thread",
               "create_merge_request_discussion_note", "add_issue_comment", "add_pull_request_comment"}
JENKINS_WRITES = {"triggerBuild", "rebuildBuild", "replayBuild", "updateBuild"}
BASH_WRITES = [
    (re.compile(r"\bgit\s+(-C\s+\S+\s+)?push\b"), "git push",
     "Undo with `git revert <commits>` in a merge request, or delete the branch if nothing was merged."),
    (re.compile(r"\bgit\s+(-C\s+\S+\s+)?(commit|revert|merge|rebase|reset|tag)\b"), "git history change",
     "Local git history: undo with `git reset`/`git revert` in that checkout (nothing left the machine unless it was pushed)."),
    (re.compile(r"\b(glab|gh)\s+(mr|pr|issue|release|api)\b"), "GitLab/GitHub CLI",
     "Check what the command created or changed in GitLab/GitHub and close or edit it there."),
    (re.compile(r"\bcurl\b[^|;&]*(-X\s*['\"]?(POST|PUT|PATCH|DELETE)|--data|--data-binary|--data-urlencode|\s-d\s|\s-F\s|--form)", re.I),
     "HTTP write", "An HTTP write: check the target system's history/revisions for what it changed."),
    (re.compile(r"\b(ssh|scp|rsync)\b"), "remote command",
     "A command on another machine: check that machine's state by hand."),
    (re.compile(r"\bdocker\s+(run|rm|stop|compose|exec)\b"), "docker", "A container change: check `docker ps` / logs on that host."),
]


# ------------------------------------------------------------------------------------ helpers

def now() -> float:
    return time.time()


def iso(ts: Optional[float]) -> Optional[str]:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None


def dumps(v: Any, limit: int = MAX_INPUT) -> Optional[str]:
    if v is None:
        return None
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= limit else json.dumps({"truncated": True, "head": s[:limit]}, ensure_ascii=False)


def loads(s: Optional[str]) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


def short_tool(tool: str) -> Tuple[str, str]:
    """mcp__<server>__<tool> → (server, tool); other tools → ("", tool)."""
    if tool.startswith("mcp__"):
        parts = tool.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
    return "", tool


def response_json(resp: Any) -> Any:
    """The JSON object inside an MCP tool response (content[].text holds it as text)."""
    if isinstance(resp, dict) and "content" in resp and isinstance(resp["content"], list):
        resp = resp["content"]
    if isinstance(resp, list):
        for part in resp:
            got = response_json(part.get("text") if isinstance(part, dict) and "text" in part else part)
            if got is not None:
                return got
        return None
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, str):
        s = resp.strip()
        for start in (s.find("{"), s.find("[")):
            if start >= 0:
                try:
                    return json.loads(s[start:])
                except ValueError:
                    continue
    return None


def first(d: Any, *keys: str) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def ids(v: Any) -> List[str]:
    """A list field (ids, or objects with an id) → sorted ids."""
    out = []
    for x in v or []:
        out.append(str(x.get("id")) if isinstance(x, dict) else str(x))
    return sorted(out)


def field_value(item: Dict[str, Any], f: str) -> Any:
    v = item.get(f)
    if f in ITEM_LIST_FIELDS:
        return ids(v)
    if isinstance(v, dict) and "id" in v:
        return v["id"]
    return v


def item_view(item: Any) -> Optional[Dict[str, Any]]:
    """The fields of a task-board item (adapter dict) the journal keeps and a revert can restore."""
    if not isinstance(item, dict) or not item.get("id"):
        return None
    if "label_ids" not in item and isinstance(item.get("labels"), list) and \
            all(isinstance(x, dict) for x in item["labels"]):
        item = {**item, "label_ids": item["labels"]}
    out = {f: field_value(item, f) for f in ITEM_FIELDS if f in item}
    out["id"] = item["id"]
    for k in ("key", "sequence_id", "project_id", "name"):
        if k in item:
            out[k] = field_value(item, k)
    if isinstance(out.get("description_html"), str):
        out["description_html"] = out["description_html"][:MAX_TEXT]
    return out


# ------------------------------------------------------------------------- config and adapters

def env_values(path: str = "~/.config/agent-ops/env") -> Dict[str, str]:
    out: Dict[str, str] = {}
    p = Path(path).expanduser()
    if not p.is_file():
        return out
    for line in p.read_text().splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$", line)
        if m:
            v = m.group(2).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[m.group(1)] = v
    return out


def code_hosts(root: Path = ROOT) -> Dict[str, Dict[str, str]]:
    """routing.yaml `code_hosts` (key → {type, url}) without pyyaml: flow maps
    (`main: { type: gitlab, url: … }`) or one-level block maps."""
    f = root / "routing.yaml"
    out: Dict[str, Dict[str, str]] = {}
    if not f.is_file():
        return out
    inside, cur = False, None
    for line in f.read_text().splitlines():
        if re.match(r"^code_hosts:\s*(#.*)?$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            break
        if not inside or not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*\{(.*)\}", line)
        if m:
            out[m.group(1)] = {k.strip(): v.strip() for k, _, v in
                               (kv.partition(":") for kv in m.group(2).split(",")) if k.strip()}
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*(#.*)?$", line)
        if m:
            cur = out.setdefault(m.group(1), {})
            continue
        m = re.match(r"^    (type|url):\s*(\S+)", line)
        if m and cur is not None:
            cur[m.group(1)] = m.group(2)
    return out


def host_key(server: str, host_type: str, hosts: Dict[str, Dict[str, str]]) -> str:
    """Which `code_hosts` key an MCP server talks to: a server named `<type>-<key>` (or `_<key>`,
    or the key itself) is that key; otherwise the first host of that type."""
    s = server.lower()
    for k, h in hosts.items():
        if h.get("type") == host_type and (s == k or s.endswith(f"-{k}") or s.endswith(f"_{k}")):
            return k
    return next((k for k, h in hosts.items() if h.get("type") == host_type), host_type)


def env_key(key: str) -> str:
    """A code_hosts key as it appears in env names: CODEHOST_<KEY>_TOKEN (upper case, - → _)."""
    return key.upper().replace("-", "_")


class Connector:
    """Builds adapter objects (infra/adapters: tracker and code host) for a given token. The one
    place the journal, agent-mcp and the CLIs get them from, so tests can pass fakes:
    `Connector(tracker_factory=lambda token: …, codehost_factory=lambda key, token: …)`."""

    def __init__(self, env: Optional[Dict[str, str]] = None, root: Path = ROOT,
                 tracker_factory=None, codehost_factory=None, hosts: Optional[Dict[str, Dict[str, str]]] = None):
        self.env = env or {}
        self.root = root
        self._tf, self._cf = tracker_factory, codehost_factory
        self.hosts = hosts if hosts is not None else code_hosts(root)
        self._cfg = None
        self._env: Dict[str, str] = dict(self.env)

    def _adapters(self):
        if str(self.root / "infra") not in sys.path:
            sys.path.insert(0, str(self.root / "infra"))
        import adapters  # noqa: E402  (infra/adapters, stdlib only)
        if self._cfg is None:
            self._cfg = adapters.load_routing(self.root)        # tracker kind and code_hosts
            self._env = {**adapters.env_values(), **self.env}   # base URL, workspace, …
        return adapters

    def tracker(self, token: Optional[str], role: str = "triage"):
        if not token:
            return None
        if self._tf:
            return self._tf(token)
        a = self._adapters()
        return a.tracker(self._cfg, token, role=role, env=self._env)

    def codehost(self, key: Optional[str], token: Optional[str], kind: str = "read"):
        """kind: read | release | revert (infra/adapters; revert tokens come only from revert.env)."""
        if not token or not key:
            return None
        if self._cf:
            return self._cf(key, token)
        a = self._adapters()
        return a.codehost(self._cfg, key, token, kind=kind, env=self._env)

    def read_codehosts(self) -> Dict[str, Any]:
        """Code-host adapters with the agents' read-only tokens (CODEHOST_<KEY>_TOKEN), for snapshots."""
        out = {}
        for k in self.hosts:
            var = f"CODEHOST_{env_key(k)}_TOKEN"
            try:
                c = self.codehost(k, self.env.get(var) or os.environ.get(var))
            except Exception:  # noqa: BLE001 - a host with no adapter just gets no snapshots
                c = None
            if c is not None:
                out[k] = c
        return out


REVERT_ENV = Path(os.environ.get("AGENT_REVERT_ENV", "~/.config/agent-ops/revert.env")).expanduser()
# A service agent's (or host script's) own change is undone with the same bot token it was made
# with: same rights, no new power. Everything else needs a person's revert token (revert.env).
BOT_TRACKER = {"triage": "TRACKER_TRIAGE_TOKEN", "analyst": "TRACKER_TRIAGE_TOKEN",
               "release": "TRACKER_RELEASE_TOKEN", "nightly": "TRACKER_NIGHTLY_TOKEN"}
BOT_CODEHOST = {"release", "post-commit-status"}      # → CODEHOST_<KEY>_RELEASE_TOKEN


class RevertClients:
    """Picks the token for each revert step. Revert tokens come only from `revert.env` (mode 600,
    read here and nowhere else: run-agent.sh never loads it, so no agent ever holds them):
    TRACKER_REVERT_TOKEN, CODEHOST_<KEY>_REVERT_TOKEN."""

    def __init__(self, env: Dict[str, str], revert: Dict[str, str], connector: Optional[Connector] = None):
        self.env, self.revert = env, revert
        if not env.get("TRACKER_NIGHTLY_TOKEN"):
            f = Path(env.get("TRACKER_NIGHTLY_TOKEN_FILE", "~/.config/agent-ops/tracker-nightly.token")).expanduser()
            if f.is_file():
                self.env = {**env, "TRACKER_NIGHTLY_TOKEN": f.read_text().strip()}
        self.connector = connector or Connector(self.env)

    def __call__(self, step: Dict[str, Any]) -> Tuple[Any, Any, str]:
        """(tracker adapter or None, code-host adapter or None, which token)."""
        agent = step.get("agent") if step.get("actor_kind") in ("service-agent", "script") else None
        if step["op"].startswith("tracker."):
            tok = self.env.get(BOT_TRACKER.get(agent or "", ""), "")
            if tok:
                return self.connector.tracker(tok, role=agent), None, f"{agent} bot token"
            return self.connector.tracker(self.revert.get("TRACKER_REVERT_TOKEN"), role="revert"), None, "revert token"
        key = step.get("host") or ""
        tok = self.env.get(f"CODEHOST_{env_key(key)}_RELEASE_TOKEN", "") if agent in BOT_CODEHOST else ""
        if tok:
            return None, self.connector.codehost(key, tok, kind="release"), f"{agent} bot token"
        return None, self.connector.codehost(key, self.revert.get(f"CODEHOST_{env_key(key)}_REVERT_TOKEN"), kind="revert"), "revert token"


# ------------------------------------------------------------------------------ classification

def classify(tool: str, ti: Any) -> Optional[Dict[str, Any]]:
    """What a tool call writes, or None for reads. The hook's own filter is only a shortcut; this
    is the rule."""
    ti = ti if isinstance(ti, dict) else {}
    server, short = short_tool(tool)
    c: Dict[str, Any] = {"tool": tool, "server": server, "entity": short, "action": short,
                         "reversible": "manual", "hint": None}

    if server and ("plane" in server or "tracker" in server):
        # Task-board MCP calls (Plane MCP vocabulary: one tool per entity with an `action`). Another
        # tracker's MCP server adds its own rules here.
        action = str(ti.get("action") or "")
        if not action or TRACKER_READ.match(action):
            return None
        wid = first(ti, "workitem_id", "work_item_id", "issue_id")
        c.update(system="tracker", action=action, project_id=ti.get("project_id"),
                 target_type="item" if short.startswith("workitem") else short,
                 target_id=wid or first(ti, f"{short}_id", "id"))
        if short == "workitem" and action in ("update", "manage_label", "manage_assignee"):
            c.update(reversible="auto", snapshot="item", kind="item_update")
        elif short == "workitem" and action == "create":
            c.update(reversible="auto", kind="item_create")
        elif short == "workitem_comment" and action in ("create", "update"):
            c.update(reversible="auto", snapshot="comment" if action == "update" else None,
                     kind="comment_" + action)
        elif short == "workitem_link" and action == "create":
            c.update(reversible="auto", kind="link_create")
        else:
            c["hint"] = f"Task board {short} {action}: undo it by hand there (the item's activity shows the change)."
        return c

    if server and ("gitlab" in server or "github" in server):
        if not WRITE_VERB.match(short):
            return None
        host_type = "github" if "github" in server else "gitlab"
        owner, repo_ = first(ti, "owner"), first(ti, "repo")
        project = f"{owner}/{repo_}" if owner and repo_ else first(ti, "project_id", "id")
        iid = first(ti, "merge_request_iid", "pullNumber", "pull_number", "issue_iid", "issue_number", "iid")
        is_mr = "merge_request" in short or "pull_request" in short or bool(first(ti, "merge_request_iid", "pullNumber", "pull_number"))
        c.update(system="codehost", host_type=host_type, project_id=project,
                 target_type=("merge_request" if is_mr else "issue") if iid else short,
                 target_id=f"{project}!{iid}" if iid and is_mr else (f"{project}#{iid}" if iid else None))
        if short in MR_CREATE:
            c.update(reversible="auto", kind="mr_create", target_type="merge_request")
        elif short in MR_UPDATE:
            c.update(reversible="auto", kind="mr_update", snapshot="merge_request")
        elif short in ISSUE_CREATE:
            c.update(reversible="auto", kind="issue_create", target_type="issue")
        elif short in ISSUE_UPDATE:
            c.update(reversible="auto", kind="issue_update", snapshot="issue")
        elif short in NOTE_CREATE:
            c.update(reversible="auto", kind="note_create", note_on="merge_request" if is_mr else "issue")
        elif short == "create_branch":
            c["hint"] = f"Delete branch {ti.get('branch')!r} in {project} if nothing else uses it."
        else:
            c["hint"] = f"Code host {short}: check {project} and undo it there (commits: `git revert` in a merge/pull request)."
        return c

    if server and "jenkins" in server:
        if short not in JENKINS_WRITES:
            return None
        c.update(system="jenkins", target_type="job", target_id=first(ti, "jobFullName", "jobName", "name"),
                 hint="Jenkins: abort the build if it is still running; a config change is restored from the "
                      "backup the Jenkins desk saved (~/jenkins-backups/<job>/).")
        return c

    if server and "metabase" in server:
        if not WRITE_VERB.match(short):
            return None
        cid = first(ti, "card_id", "dashboard_id", "collection_id", "id")
        c.update(system="metabase", target_type=short.split("_", 1)[-1], target_id=cid,
                 hint=f"Metabase: open {short.split('_', 1)[-1]} {cid} → History and revert to the previous revision.")
        return c

    if server and "figma" in server:
        if not WRITE_VERB.match(short) and short not in ("use_figma", "send_code_to_figma"):
            return None
        c.update(system="figma", target_type="file", target_id=first(ti, "fileKey", "file_key"),
                 hint="Figma: File → Show version history and restore the version before this change.")
        return c

    if server and "agent-mcp" in server:
        if short != "send_message":
            return None
        c.update(system="telegram", target_type="message", target_id=",".join(map(str, ti.get("to") or [])) if
                 isinstance(ti.get("to"), list) else str(ti.get("to") or ""), reversible="no",
                 hint="A sent Telegram message can't be unsent: send a correction to the same people.")
        return c

    if tool == "Artifact":
        action = str(ti.get("action") or "publish")
        if action in ("list", "read", "quickstart", "open"):
            return None
        c.update(system="artifact", action=action, target_type="artifact", target_id=ti.get("url") or ti.get("file_path"),
                 hint="Artifact: republish the previous version (the publish result names each version id), or "
                      "delete the artifact if it should not exist.")
        return c
    if tool in ("ArtifactData", "ArtifactComments"):
        action = str(ti.get("action") or "")
        if action in ("get", "list", "query", "read", ""):
            return None
        c.update(system="artifact", action=action, target_type=tool, target_id=ti.get("url"),
                 hint=f"{tool} {action}: undo it with the opposite {tool} call (re-set the old document, delete the comment).")
        return c

    if tool == "Bash":
        cmd = str(ti.get("command") or "")
        for rx, what, hint in BASH_WRITES:
            if rx.search(cmd):
                c.update(system="shell", action=what, target_type="command", target_id=None, hint=hint,
                         reversible="manual")
                return c
        return None
    return None


def created_id(c: Dict[str, Any], resp: Any) -> Optional[str]:
    j = response_json(resp)
    if isinstance(j, dict) and set(j) == {"result"} and isinstance(j["result"], (dict, list)):
        j = j["result"]          # FastMCP servers wrap the object: {"result": {...}}
    if isinstance(j, list):
        j = j[0] if j and isinstance(j[0], dict) else None
    if not isinstance(j, dict):
        return None
    if c.get("system") == "codehost":
        if c["entity"] == "create_merge_request_thread":
            notes = j.get("notes") or []
            return str(notes[0]["id"]) if notes and isinstance(notes[0], dict) and notes[0].get("id") else None
        if c.get("kind") in ("mr_create", "issue_create"):
            v = first(j, "iid", "number")
        else:
            v = first(j, "id")
        return str(v) if v is not None else None
    v = first(j, "id")
    return str(v) if v is not None else None


# -------------------------------------------------------------------------------------- journal

class Journal:
    def __init__(self, path: Path = DEFAULT_DB, projects: Optional[Dict[str, Dict[str, Any]]] = None,
                 hosts: Optional[Dict[str, Dict[str, str]]] = None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self.projects = projects or {}
        self.key_by_pid = {p["tracker_project_id"]: k for k, p in self.projects.items() if p.get("tracker_project_id")}
        self.hosts = hosts if hosts is not None else code_hosts()
        self.lock = threading.Lock()   # one connection, used from agent-mcp's worker threads

    def q(self, sql: str, *args: Any) -> List[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # ---- ingest ---------------------------------------------------------------------------------
    def ingest(self, phase: str, payload: Dict[str, Any], actor: Dict[str, Any], tracker: Any = None,
               codehosts: Optional[Dict[str, Any]] = None, redact=lambda x: x) -> Optional[str]:
        """One hook call. `pre` saves the item before the write; `post` records the event.
        `tracker`: a tracker adapter with the caller's token; `codehosts`: code-host adapters by
        `code_hosts` key (read tokens). Returns the event id (post) or None."""
        tool = str(payload.get("tool_name") or "")
        ti = payload.get("tool_input") or {}
        c = classify(tool, ti)
        if c is None:
            return None
        if c.get("system") == "codehost":
            c["host"] = host_key(c.get("server", ""), c.get("host_type", ""), self.hosts)
        tuid = str(payload.get("tool_use_id") or "")
        self.q("delete from pending where created_at < ?", now() - PENDING_TTL)
        if phase == "pre":
            before = self._snapshot(c, ti, tracker, codehosts)
            if tuid and before is not None:
                self.q("insert or replace into pending (tool_use_id, created_at, before) values (?,?,?)",
                       tuid, now(), dumps(before, MAX_TEXT * 2))
            return None
        before = None
        if tuid:
            row = self.q("select before from pending where tool_use_id=?", tuid)
            if row:
                before = loads(row[0]["before"])
                self.q("delete from pending where tool_use_id=?", tuid)
        resp = payload.get("tool_response")
        ok = not _is_error(resp)
        new_id = created_id(c, resp) if ok else None
        after = self._after(c, ti, new_id, tracker) if ok else None
        if c.get("system") == "tracker" and c.get("target_type") == "item" and c.get("target_id") and tracker \
                and not after and not (before or {}).get("key") and c.get("project_id"):
            row = self.q("select 1 from events where target_id=? and target_key like '%-%' limit 1", str(c["target_id"]))
            if not row:
                try:
                    v = item_view(tracker.get_item(c["project_id"], c["target_id"])) or {}
                    k = self._item_key(c, v)
                    if k:
                        c = {**c, "target_key": k}
                except Exception:  # noqa: BLE001
                    pass
        return self.record(c, actor, ti=ti, before=before, after=after, result=resp, ok=ok, new_id=new_id,
                           session=payload.get("session_id"), cwd=payload.get("cwd"),
                           transcript=payload.get("transcript_path"), redact=redact)

    def _snapshot(self, c, ti, tracker, codehosts) -> Any:
        try:
            if c.get("snapshot") == "item" and tracker and c.get("project_id") and c.get("target_id"):
                return item_view(tracker.get_item(c["project_id"], c["target_id"]))
            if c.get("snapshot") == "comment" and tracker:
                cid = str(first(ti, "comment_id", "id"))
                for cm in tracker.list_comments(c["project_id"], c["target_id"]) or []:
                    if str(cm.get("id")) == cid:
                        return {"id": cid, "comment_html": cm.get("comment_html") or cm.get("body")}
                return {"id": cid, "snapshot_error": "comment not found"}
            if c.get("snapshot") in ("merge_request", "issue") and codehosts and codehosts.get(c["host"]):
                ch = codehosts[c["host"]]
                iid = re.split(r"[!#]", str(c.get("target_id") or ""))[-1]
                getter = ch.get_merge_request if c["snapshot"] == "merge_request" else getattr(ch, "get_issue", None)
                if getter is None:
                    return {"snapshot_error": "this code host adapter can't read issues"}
                j = getter(c["project_id"], iid)
                return {k: j.get(k) for k in ("title", "description", "labels", "state", "target_branch",
                                               "assignees", "reviewers") if isinstance(j, dict) and k in j}
        except Exception as e:  # noqa: BLE001 - a failed snapshot must never stop the write
            return {"snapshot_error": str(e)[:200]}
        return None

    def _after(self, c, ti, new_id, tracker) -> Any:
        try:
            if c.get("system") == "tracker" and c["entity"] == "workitem" and tracker and c.get("project_id"):
                wid = c.get("target_id") or new_id
                if wid:
                    return item_view(tracker.get_item(c["project_id"], wid))
        except Exception as e:  # noqa: BLE001
            return {"snapshot_error": str(e)[:200]}
        return None

    def _item_key(self, c, item) -> Optional[str]:
        if isinstance(item, dict) and item.get("key"):
            return str(item["key"])
        if isinstance(item, dict) and item.get("sequence_id"):
            k = self.key_by_pid.get(str(c.get("project_id")))
            if k:
                return f"{k}-{item['sequence_id']}"
        return None

    def record(self, c: Dict[str, Any], actor: Dict[str, Any], *, ti: Any = None, before: Any = None, after: Any = None,
               result: Any = None, ok: bool = True, new_id: Optional[str] = None, session: Any = None,
               cwd: Any = None, transcript: Any = None, summary: Optional[str] = None, redact=lambda x: x,
               revert_of: Optional[str] = None) -> str:
        eid = uuid.uuid4().hex[:12]
        target_id = c.get("target_id") or new_id
        target_key = c.get("target_key") or self._key(c, before, after, target_id)
        if c.get("entity") in ("workitem_comment", "workitem_link", "workitem_relation") or c.get("kind") == "note_create":
            c = {**c, "created": new_id}
        safe_ti = ti
        if c.get("system") == "telegram" and isinstance(ti, dict):   # keep who/whom, not the message
            safe_ti = {k: v for k, v in ti.items() if k != "text"}
            safe_ti["text_chars"] = len(str(ti.get("text") or ""))
            result = None
        summary = summary or self._summary(c, target_key, before, after, ok)
        self.q("""insert into events (id, ts, actor_kind, person, user_id, email, role, agent, run_id, session_id,
                    client, cwd, transcript, tool, action, system, project, target_type, target_id, target_key,
                    summary, input, before, after, result, ok, reversible, hint, revert_of)
                  values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               eid, now(), actor.get("kind") or "person-agent", actor.get("person"), actor.get("user_id"),
               actor.get("email"), actor.get("role"), actor.get("agent"), actor.get("run_id"),
               str(session) if session else None, actor.get("client"), str(cwd) if cwd else None,
               str(transcript) if transcript else None, c["tool"], c.get("action"), c.get("system"),
               self.key_by_pid.get(str(c.get("project_id")), c.get("project")),
               c.get("target_type"), str(target_id) if target_id else None, target_key, redact(summary),
               redact(dumps({"input": safe_ti, "cls": c})), dumps(before, MAX_TEXT * 2), dumps(after, MAX_TEXT * 2),
               redact(dumps(result)), 1 if ok else 0, c.get("reversible", "manual") if ok else "no",
               c.get("hint"), revert_of)
        return eid

    def _key(self, c, before, after, target_id) -> Optional[str]:
        if c.get("system") == "tracker":
            k = self._item_key(c, after) or self._item_key(c, before)
            if k:
                return k
            if target_id:   # a comment/link on an item seen before
                row = self.q("select target_key from events where target_id=? and target_key like '%-%' "
                             "and system='tracker' order by ts desc limit 1", str(target_id))
                if row:
                    return row[0]["target_key"]
        if c.get("system") == "codehost":
            proj = urllib.parse.unquote(str(c.get("project_id") or ""))
            if target_id and c.get("kind") in ("mr_create", "issue_create") and proj:
                return f"{proj}{'!' if c['kind'] == 'mr_create' else '#'}{target_id}"
            if c.get("target_id"):
                return urllib.parse.unquote(str(c["target_id"]))
            return proj or None
        return str(target_id) if target_id else None

    @staticmethod
    def _summary(c, key, before, after, ok) -> str:
        what = f"{c.get('system') or c['tool']} {c.get('entity')} {c.get('action')}".replace(f" {c.get('action')} {c.get('action')}", "")
        if c.get("entity") == c.get("action"):
            what = f"{c.get('system') or c['tool']} {c.get('action')}"
        s = f"{what} {key or ''}".strip()
        if isinstance(before, dict) and isinstance(after, dict):
            changed = [f for f in ITEM_FIELDS if f in after and before.get(f) != after.get(f)]
            if changed:
                s += " (" + ", ".join(changed) + ")"
        return s + ("" if ok else " [failed]")

    # ---- queries --------------------------------------------------------------------------------
    def events(self, *, target: Optional[str] = None, session: Optional[str] = None, run: Optional[str] = None,
               event: Optional[str] = None, actor: Optional[str] = None, since: Optional[float] = None,
               until: Optional[float] = None, text: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        sql, args = "select * from events where 1=1", []
        if event:
            sql += " and id=?"; args.append(event)
        if session:
            sql += " and session_id=?"; args.append(session)
        if run:
            sql += " and run_id=?"; args.append(run)
        if target:
            sql += " and (target_key=? or target_id=?)"; args += [target, target]
        if actor:
            sql += " and (person=? or agent=? or email=?)"; args += [actor, actor, actor]
        if since:
            sql += " and ts>=?"; args.append(since)
        if until:
            sql += " and ts<=?"; args.append(until)
        if text:
            sql += " and (summary like ? or input like ? or target_key like ?)"; args += [f"%{text}%"] * 3
        sql += " order by ts desc limit ?"; args.append(limit)
        return [dict(r) for r in self.q(sql, *args)]

    def blame(self, target: str, since: Optional[float] = None, tracker: Any = None,
              names: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        """Every recorded change to `target` (newest first). For a task-board item, the item's own
        activity is merged in (tracker adapter `activities`): changes with no matching journal event
        were made by hand (or by a script that doesn't report to the journal)."""
        evs = self.events(target=target, since=since, limit=500)
        rows = [_blame_row(e) for e in evs]
        if tracker and re.match(r"^[A-Z][A-Z0-9]*-\d+$", target):
            pid = next((p.get("tracker_project_id") for k, p in self.projects.items() if target.startswith(k + "-")), None)
            wid = next((e["target_id"] for e in evs if e.get("target_id")), None)
            if pid and not wid:
                try:
                    wid = (tracker.get_item_by_key(target) or {}).get("id")
                except Exception:  # noqa: BLE001
                    wid = None
            if pid and wid:
                try:
                    acts = tracker.activities(pid, wid)
                except Exception:  # noqa: BLE001
                    acts = []
                for a in acts or []:
                    ts = _parse_ts(a.get("created_at"))
                    if since and ts and ts < since:
                        continue
                    actor = a.get("actor")
                    uid = str((actor.get("id") if isinstance(actor, dict) else actor) or (a.get("actor_detail") or {}).get("id") or "")
                    matched = any(e["user_id"] == uid and abs((ts or 0) - e["ts"]) < 180 for e in evs if e["user_id"])
                    if matched:
                        continue
                    who = ((actor.get("display_name") if isinstance(actor, dict) else None)
                           or (a.get("actor_detail") or {}).get("display_name") or (names or {}).get(uid) or uid or "someone")
                    rows.append({"when": iso(ts), "ts": ts or 0, "who": who, "how": "by hand on the task board (no agent recorded)",
                                 "what": f"{a.get('field') or a.get('verb') or 'change'}: {a.get('old_value')} → {a.get('new_value')}",
                                 "event": None, "undo": "by hand"})
        rows.sort(key=lambda r: r["ts"], reverse=True)
        return rows

    # ---- revert ---------------------------------------------------------------------------------
    def plan(self, selector: Dict[str, Any], created_by: str = "") -> Dict[str, Any]:
        sel = {k: v for k, v in selector.items() if v}
        if not sel:
            raise ValueError("say what to revert: an event, run, session, or a target/actor with a time window")
        evs = self.events(target=sel.get("target"), session=sel.get("session"), run=sel.get("run"),
                          event=sel.get("event"), actor=sel.get("actor"), since=sel.get("since"),
                          until=sel.get("until"), limit=1000)
        evs = [e for e in evs if not e["reverted_by"] and (sel.get("event") or not e["revert_of"]) and e["ok"]]
        steps = []
        for e in evs:  # newest first: undo in reverse order
            steps.extend(_steps_for(e))
        pid = "rv-" + uuid.uuid4().hex[:8]
        self.q("insert into plans (id, created_at, created_by, selector, steps, status) values (?,?,?,?,?,?)",
               pid, now(), created_by, dumps(sel), dumps(steps, 10 * MAX_TEXT), "planned")
        return {"plan": pid, "events": len(evs), "steps": steps,
                "auto": sum(1 for s in steps if s["op"] != "manual"),
                "manual": sum(1 for s in steps if s["op"] == "manual"),
                "apply": f"on the agent host: infra/bin/journal revert-apply {pid}"}

    def apply(self, plan_id: str, applied_by: str, tracker: Any = None, codehosts: Optional[Dict[str, Any]] = None,
              clients=None) -> Dict[str, Any]:
        """`clients(step) -> (tracker, codehost, which token)` picks a token per step (RevertClients);
        without it, `tracker` and `codehosts` (by key) are used for every step."""
        row = self.q("select * from plans where id=?", plan_id)
        if not row:
            raise ValueError(f"no plan {plan_id}")
        p = dict(row[0])
        if p["status"] == "applied":
            raise ValueError(f"plan {plan_id} was already applied")
        steps = loads(p["steps"]) or []
        results = []
        for s in steps:
            if s["op"] == "manual":
                results.append({**s, "result": "manual: do it by hand", "ok": None})
                continue
            try:
                if clients:
                    st, sc, who = clients(s)
                else:
                    st, sc, who = tracker, (codehosts or {}).get(s.get("host") or ""), "given token"
                note = f"{_apply_step(s, st, sc)} (with the {who})"
                results.append({**s, "result": note, "ok": True})
                ev = self.q("select * from events where id=?", s["event"])
                c = {"tool": "journal.revert", "system": s["op"].split(".")[0], "entity": "revert", "action": s["op"],
                     "target_id": s.get("target_id"), "target_key": s.get("target_key"),
                     "project": ev[0]["project"] if ev else None, "reversible": "manual",
                     "hint": f"This undid event {s['event']}; to undo the revert, redo that change by hand."}
                self.record(c, {"kind": "revert", "person": applied_by}, ti=s, summary=f"revert {s['op']} {s.get('target_key') or ''}: {note}",
                            revert_of=s["event"])
                self.q("update events set reverted_by=? where id=?", plan_id, s["event"])
            except Exception as e:  # noqa: BLE001
                results.append({**s, "result": f"failed: {str(e)[:300]}", "ok": False})
        bad = any(r["ok"] is False for r in results)
        self.q("update plans set status=?, applied_at=?, applied_by=?, results=? where id=?",
               "partly-applied" if bad else "applied", now(), applied_by, dumps(results, 10 * MAX_TEXT), plan_id)
        return {"plan": plan_id, "results": results}


def _is_error(resp: Any) -> bool:
    if isinstance(resp, dict):
        if resp.get("is_error") or resp.get("isError"):
            return True
        if resp.get("success") is False:
            return True
    if isinstance(resp, str) and re.match(r"^\s*(error|Error:|MCP error)", resp):
        return True
    return False


def _parse_ts(s: Any) -> Optional[float]:
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _blame_row(e: Dict[str, Any]) -> Dict[str, Any]:
    if e["actor_kind"] == "service-agent":
        who, how = f"{e['agent']} agent", f"service agent run {e['run_id'] or '?'} (session {e['session_id'] or '?'})"
    elif e["actor_kind"] == "script":
        who, how = e["agent"] or "script", "script on the agent host"
    elif e["actor_kind"] == "revert":
        who, how = e["person"] or "?", "revert (journal)"
    else:
        who = e["person"] or e["email"] or "?"
        how = f"{who}'s agent ({e['client'] or 'claude-code'}, {e['role'] or 'no role'}), session {e['session_id'] or '?'}"
    return {"when": iso(e["ts"]), "ts": e["ts"], "who": who, "how": how, "what": e["summary"], "event": e["id"],
            "transcript": e["transcript"], "undo": {"auto": "revert-plan", "manual": e["hint"] or "by hand",
                                                    "no": e["hint"] or "can't be undone"}[e["reversible"]]
            + (f" (reverted by {e['reverted_by']})" if e["reverted_by"] else "")}


def _steps_for(e: Dict[str, Any]) -> List[Dict[str, Any]]:
    cls = (loads(e["input"]) or {}).get("cls") or {}
    before, after = loads(e["before"]), loads(e["after"])
    base = {"event": e["id"], "target_key": e["target_key"], "target_id": e["target_id"], "what": e["summary"],
            "when": iso(e["ts"]), "actor_kind": e["actor_kind"], "agent": e["agent"]}
    if e["reversible"] != "auto":
        return [{**base, "op": "manual", "hint": e["hint"] or "undo by hand"}]
    sysname, entity, kind = cls.get("system"), cls.get("entity"), cls.get("kind")
    if sysname == "tracker" and kind == "item_update":
        if not isinstance(before, dict) or "snapshot_error" in before or not isinstance(after, dict):
            return [{**base, "op": "manual", "hint": "no saved 'before' value: check the item's activity on the task board and undo by hand"}]
        fields: Dict[str, Any] = {}
        for f in ITEM_FIELDS:
            if f in after and f in before and after.get(f) != before.get(f):
                if f in ITEM_LIST_FIELDS:
                    fields[f] = {"add": sorted(set(before[f]) - set(after[f])), "remove": sorted(set(after[f]) - set(before[f]))}
                else:
                    fields[f] = {"set": before[f], "expect": after[f]}
        if not fields:
            return []
        return [{**base, "op": "tracker.restore", "project_id": cls.get("project_id"), "fields": fields}]
    if sysname == "tracker" and kind == "item_create":
        return [{**base, "op": "tracker.cancel", "project_id": cls.get("project_id")}]
    if sysname == "tracker" and kind == "comment_create" and cls.get("created"):
        return [{**base, "op": "tracker.delete_comment", "project_id": cls.get("project_id"), "comment_id": cls["created"]}]
    if sysname == "tracker" and kind == "comment_update" and isinstance(before, dict) and "snapshot_error" not in before:
        return [{**base, "op": "tracker.restore_comment", "project_id": cls.get("project_id"),
                 "comment_id": before.get("id"), "html": before.get("comment_html")}]
    if sysname == "tracker" and kind == "link_create" and cls.get("created"):
        return [{**base, "op": "tracker.delete_link", "project_id": cls.get("project_id"), "link_id": cls["created"]}]
    if sysname == "codehost":
        g = {"host": cls.get("host"), "project": cls.get("project_id")}
        if kind == "mr_create" and e["target_id"]:
            return [{**base, **g, "op": "codehost.close", "noun": "merge_request", "iid": str(e["target_id"]).split("!")[-1]}]
        if kind == "issue_create" and e["target_id"]:
            return [{**base, **g, "op": "codehost.close", "noun": "issue", "iid": str(e["target_id"]).split("#")[-1]}]
        if kind in ("mr_update", "issue_update") and isinstance(before, dict) and "snapshot_error" not in before:
            iid = re.split(r"[!#]", str(e["target_id"]))[-1]
            fields = {k: before[k] for k in ("title", "description", "labels", "target_branch") if k in before}
            return [{**base, **g, "op": "codehost.restore", "noun": cls.get("snapshot"), "iid": iid, "fields": fields}]
        if kind == "note_create" and cls.get("created"):
            iid = re.split(r"[!#]", str(e["target_id"] or ""))[-1]
            return [{**base, **g, "op": "codehost.delete_note", "noun": cls.get("note_on") or "merge_request",
                     "iid": iid, "note_id": cls["created"]}]
        if entity == "commit_status" and isinstance(before, dict):
            return [{**base, **g, "op": "codehost.status", "sha": before.get("sha"), "name": before.get("name"),
                     "state": before.get("state") or "canceled"}]
    return [{**base, "op": "manual", "hint": e["hint"] or "undo by hand"}]


def _apply_step(s: Dict[str, Any], tracker: Any, codehost: Any) -> str:
    """One revert step through the adapters (infra/adapters/README.md)."""
    op = s["op"]
    if op.startswith("tracker."):
        if not tracker:
            raise RuntimeError("no task-board token for this step: put TRACKER_REVERT_TOKEN in ~/.config/agent-ops/revert.env")
        pid, wid = s["project_id"], s["target_id"]
        if op == "tracker.restore":
            cur = item_view(tracker.get_item(pid, wid)) or {}
            patch, conflicts = {}, []
            for f, ch in s["fields"].items():
                if "set" in ch:
                    if cur.get(f) == ch["expect"]:
                        patch[f] = ch["set"]
                    elif cur.get(f) != ch["set"]:
                        conflicts.append(f)
                else:
                    now_ids = set(cur.get(f) or [])
                    new = sorted((now_ids - set(ch["remove"])) | set(ch["add"]))
                    if new != sorted(now_ids):
                        patch[f] = new
            if patch:
                tracker.update_item(pid, wid, patch)
            note = f"restored {', '.join(patch) or 'nothing (already as before)'}"
            if conflicts:
                note += f"; left {', '.join(conflicts)} alone (changed again since; check by hand)"
            return note
        if op == "tracker.cancel":
            states = tracker.states(pid)
            cancelled = next((st for st in states or [] if str(st.get("group")).lower() == "cancelled"), None)
            if not cancelled:
                raise RuntimeError("the project has no cancelled state")
            tracker.update_item(pid, wid, {"state": cancelled["id"]})
            tracker.add_comment(pid, wid, f"<p>[revert] Cancelled: undoes journal event {s['event']} (created by mistake).</p>")
            return "moved to cancelled"
        if op == "tracker.delete_comment":
            tracker.delete_comment(pid, wid, s["comment_id"])
            return "comment deleted"
        if op == "tracker.restore_comment":
            tracker.update_comment(pid, wid, s["comment_id"], s.get("html") or "")
            return "comment text restored"
        if op == "tracker.delete_link":
            tracker.delete_link(pid, wid, s["link_id"])
            return "link removed"
    if op.startswith("codehost."):
        key = s.get("host") or "?"
        if not codehost:
            raise RuntimeError(f"no code-host token for {key}: put CODEHOST_{env_key(key)}_REVERT_TOKEN in ~/.config/agent-ops/revert.env")
        repo = urllib.parse.unquote(str(s["project"]))
        if op == "codehost.close":
            if s["noun"] == "merge_request":
                codehost.close_merge_request(repo, s["iid"])
            else:
                close = getattr(codehost, "close_issue", None)
                if close is None:
                    raise RuntimeError("this code host adapter can't close issues: close it by hand")
                close(repo, s["iid"])
            return "closed"
        if op == "codehost.restore":
            if s["noun"] == "merge_request":
                codehost.update_merge_request(repo, s["iid"], s["fields"])
            else:
                upd = getattr(codehost, "update_issue", None)
                if upd is None:
                    raise RuntimeError("this code host adapter can't edit issues: restore it by hand")
                upd(repo, s["iid"], s["fields"])
            return f"restored {', '.join(s['fields'])}"
        if op == "codehost.delete_note":
            codehost.delete_note(repo, s["noun"], s["iid"], s["note_id"])
            return "note deleted"
        if op == "codehost.status":
            codehost.post_status(repo, s["sha"], s["state"], s.get("name") or "release-agent",
                                 f"reverted (journal {s['event']})")
            return f"status set back to {s['state']}"
    raise RuntimeError(f"unknown step {op}")


# ------------------------------------------------------------------------------------- suspects

def suspects(term: str, repos: Iterable[Dict[str, Any]], since_days: float = 7, journal: Optional[Journal] = None,
             limit: int = 15, upstream: Optional[List[str]] = None,
             upstream_repos: Optional[Iterable[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Recent changes that touch `term` (a table, file, route, page or metric name): commits in the
    project's repo clones whose diff adds or removes the term (git log -S) or whose paths contain
    it, and journal events that mention it. `upstream`: names the term is built from (from
    infra/lineage: views, tables, code files), searched the same way in `upstream_repos` and ranked
    a little lower. Newest and closest first."""
    term = term.strip()
    if len(term) < 3:
        raise ValueError("give a table, file, route or page name of at least 3 characters")
    since = f"{int(since_days * 24)}.hours.ago"
    hits: Dict[str, Dict[str, Any]] = {}
    terms = [(term, list(repos), 0.0)] + [(u, list(upstream_repos or repos), 0.5) for u in (upstream or [])[:12]
                                          if u and len(u) >= 3 and u != term]
    defaults: Dict[str, Set[str]] = {}
    for t, rlist, penalty in terms:
        for r in rlist:
            path = r.get("path")
            if not path or not Path(path).is_dir():
                continue
            scope = [r["only"]] if r.get("only") else []
            if path not in defaults:
                defaults[path] = set(_git(path, "log", "--format=%H", f"--since={since}", "HEAD", *(["--", *scope] if scope else [])).split())
            for how, extra in (("changes the text", ["-S", t]), ("path matches", [])):
                args = ["log", "--all", f"--since={since}", "--format=%H%x1f%an%x1f%aI%x1f%s%x1f%b%x1e", *extra]
                if how == "path matches":
                    args += ["--", f":(glob)**/*{t.split('/')[-1]}*"]
                elif scope:
                    args += ["--", *scope]
                for rec in _git(path, *args).split("\x1e"):
                    parts = rec.strip("\n").split("\x1f")
                    if len(parts) < 5 or not parts[0]:
                        continue
                    sha, author, date, subject, body = parts
                    h = hits.setdefault(sha, {"kind": "commit", "repo": r.get("name"), "sha": sha[:10], "author": author,
                                              "when": date, "what": subject, "why": [],
                                              "on_default_branch": sha in defaults[path],
                                              "agent_assisted": bool(re.search(r"Co-Authored-By: Claude|Generated with \[Claude", body, re.I)),
                                              "score": 0.0})
                    why = how if t == term else f"{how}: {t} (upstream)"
                    if why not in h["why"]:
                        h["why"].append(why)
                        h["score"] += (2 if how == "changes the text" else 1) - penalty
    out = list(hits.values())
    for h in out:
        h["score"] += 2 if h["on_default_branch"] else 0
        ts = _parse_ts(h["when"]) or 0
        h["score"] += max(0.0, 1 - (now() - ts) / (since_days * 86400))
    if journal:
        for e in journal.events(text=term, since=now() - since_days * 86400, limit=50):
            row = _blame_row(e)
            out.append({"kind": "journal", "event": e["id"], "author": row["who"], "how": row["how"], "when": row["when"],
                        "what": e["summary"], "why": ["mentions it"], "score": 1.5 + max(0.0, 1 - (now() - e["ts"]) / (since_days * 86400))})
    out.sort(key=lambda h: (h["score"], h["when"] or ""), reverse=True)
    return out[:limit]


def _git(path: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ------------------------------------------------------------------------------------------ CLI

def _projects(root: Path) -> Dict[str, Dict[str, Any]]:
    """projects.yaml without pyyaml: only key → tracker_project_id and repos (name, path, only)."""
    f = root / "projects.yaml"
    out: Dict[str, Dict[str, Any]] = {}
    if not f.is_file():
        return out
    cur = None
    repo = None
    for line in f.read_text().splitlines():
        if re.match(r"^\s*#", line) or not line.strip():
            continue
        m = re.match(r"^  ([A-Z][A-Z0-9]*):\s*$", line)
        if m:
            cur = out.setdefault(m.group(1), {"key": m.group(1), "tracker_project_id": "", "repos": []})
            continue
        if cur is None:
            continue
        m = re.match(r"^    tracker_project_id:\s*(\S+)", line)
        if m:
            cur["tracker_project_id"] = m.group(1)
            continue
        m = re.match(r"^      - name:\s*(\S+)", line)
        if m:
            repo = {"name": m.group(1)}
            cur["repos"].append(repo)
            continue
        m = re.match(r"^        (path|only):\s*(\S+)", line)
        if m and repo is not None:
            repo[m.group(1)] = m.group(2)
    return out


def _since(v: Optional[str]) -> Optional[float]:
    if not v:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)([hd])$", v)
    if m:
        return now() - float(m.group(1)) * (3600 if m.group(2) == "h" else 86400)
    t = _parse_ts(v if "T" in v else v + "T00:00:00+00:00")
    if t is None:
        raise SystemExit(f"bad time {v!r}: use 6h, 3d or 2026-11-01 / an ISO time")
    return t


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv: Optional[List[str]] = None) -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(prog="journal", description="Who changed what, and how to undo it.")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("blame", help="who changed a task / MR / PR / target")
    b.add_argument("target"); b.add_argument("--since")
    s = sub.add_parser("suspects", help="recent changes that touch a table/file/route/page")
    s.add_argument("project"); s.add_argument("term"); s.add_argument("--days", type=float, default=7)
    s.add_argument("--no-upstream", action="store_true", help="don't also search what the term is built from (lineage)")
    ls = sub.add_parser("show", help="list events")
    for a in ("--target", "--session", "--run", "--event", "--actor", "--since", "--until", "--text"):
        ls.add_argument(a)
    ls.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("revert-plan", help="dry run: what a revert would undo")
    for a in ("--target", "--session", "--run", "--event", "--actor", "--since", "--until"):
        p.add_argument(a)
    ap_ = sub.add_parser("revert-apply", help="carry out a plan (agent host only, asks you to type the plan id)")
    ap_.add_argument("plan"); ap_.add_argument("--as", dest="who", default=os.environ.get("USER", "?"))
    r = sub.add_parser("record", help="record a write a script made (for scripts on the agent host)")
    r.add_argument("--agent", required=True, help="script name, e.g. post-commit-status")
    # --system codehost (with --host <code_hosts key>) | tracker | telegram | …
    r.add_argument("--system", required=True); r.add_argument("--action", required=True)
    r.add_argument("--target-type"); r.add_argument("--target-id"); r.add_argument("--target-key")
    r.add_argument("--project"); r.add_argument("--host", default="main"); r.add_argument("--project-id")
    r.add_argument("--summary"); r.add_argument("--before"); r.add_argument("--after")
    r.add_argument("--reversible", default="manual", choices=["auto", "manual", "no"]); r.add_argument("--hint")
    r.add_argument("--run", default=os.environ.get("AGENT_RUN_ID"))
    args = ap.parse_args(argv)

    j = Journal(Path(args.db), _projects(root), code_hosts(root))
    env = env_values(os.environ.get("AGENT_OPS_ENV", "~/.config/agent-ops/env"))
    connector = Connector(env, root)
    if args.cmd == "blame":
        tok = env.get("TRACKER_TRIAGE_TOKEN") or os.environ.get("TRACKER_TRIAGE_TOKEN")
        try:
            tracker = connector.tracker(tok)
        except Exception:  # noqa: BLE001 - blame still works from the journal alone
            tracker = None
        _print(j.blame(args.target, _since(args.since), tracker))
    elif args.cmd == "suspects":
        proj = j.projects.get(args.project.upper())
        if not proj:
            raise SystemExit(f"unknown project {args.project}")
        up: List[str] = []
        if not args.no_upstream:
            try:
                sys.path.insert(0, str(root / "infra" / "lineage"))
                import lineage as L
                up = L.upstream_terms(L.load(), args.term)
            except Exception:  # noqa: BLE001 - suspects still works without lineage
                up = []
        all_repos = [r for p_ in j.projects.values() for r in p_["repos"]]
        _print(suspects(args.term, proj["repos"], args.days, j, upstream=up, upstream_repos=all_repos))
    elif args.cmd == "show":
        evs = j.events(target=args.target, session=args.session, run=args.run, event=args.event, actor=args.actor,
                       since=_since(args.since), until=_since(args.until), text=args.text, limit=args.limit)
        _print([{**_blame_row(e), "reversible": e["reversible"], "target": e["target_key"]} for e in evs])
    elif args.cmd == "revert-plan":
        _print(j.plan({"target": args.target, "session": args.session, "run": args.run, "event": args.event,
                       "actor": args.actor, "since": _since(args.since), "until": _since(args.until)},
                      created_by=os.environ.get("USER", "")))
    elif args.cmd == "revert-apply":
        if not sys.stdin.isatty():
            raise SystemExit("revert-apply runs only in a terminal, typed by a person (never from an agent)")
        row = j.q("select steps from plans where id=?", args.plan)
        if not row:
            raise SystemExit(f"no plan {args.plan}")
        _print(loads(row[0]["steps"]))
        typed = input(f"Type the plan id ({args.plan}) to apply these steps: ").strip()
        if typed != args.plan:
            raise SystemExit("not applied")
        revert = env_values(str(REVERT_ENV))
        if REVERT_ENV.is_file() and REVERT_ENV.stat().st_mode & 0o077:
            raise SystemExit(f"{REVERT_ENV} must be readable by you only: chmod 600 {REVERT_ENV}")
        _print(j.apply(args.plan, args.who, clients=RevertClients(env, revert, connector)))
    elif args.cmd == "record":
        system, host_type = args.system, None
        if system in ("gitlab", "github"):      # a code-host type (post-commit-status.py passes it)
            system, host_type = "codehost", args.system
        c = {"tool": f"script:{args.agent}", "system": system, "host_type": host_type, "entity": args.action, "action": args.action,
             "target_type": args.target_type, "target_id": args.target_id, "target_key": args.target_key,
             "project": args.project, "project_id": args.project_id, "host": args.host,
             "reversible": args.reversible, "hint": args.hint}
        eid = j.record(c, {"kind": "script", "agent": args.agent, "run_id": args.run, "client": "script"},
                       before=loads(args.before), after=loads(args.after), summary=args.summary)
        print(eid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
