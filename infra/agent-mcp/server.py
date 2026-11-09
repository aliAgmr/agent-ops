# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==1.30.0", "pyyaml==6.0.2"]
# ///
"""agent-mcp: a remote MCP server that teammates' agents (Claude Code, Cursor, Codex, Kimi) call to
ask questions about a whole project (code, data, CI, the code host, the task board) they may not
have access to.

Each conversation is one resumable headless Claude Code session run in the read-only answering
profile (service/agent-mcp). Callers authenticate with their own task-board token (the tracker
adapter in infra/adapters checks it: who they are, which projects they belong to); they may only
use projects (projects.yaml, `tracker_project_id`) they are a member of, and only their own
conversations. Answers pass a hard filter that replaces every known secret.

It also lets a teammate's agent message other teammates (send_message): one-way Telegram DMs
from the team bot, sent as the caller (identity from their token → people.yaml), with only
who/whom/when kept, never the text.

And it keeps the team's action journal (infra/journal): every agent's write tool calls arrive at
POST /journal from the core plugin's journal.sh hook; blame / suspects / revert_plan read it.

Run:  uv run --script infra/agent-mcp/server.py      (see infra/agent-mcp/README.md)
"""

from __future__ import annotations

import asyncio
import base64
import html
import contextlib
import glob
import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

HERE = Path(__file__).resolve().parent
log = logging.getLogger("agent-mcp")
sys.path.insert(0, str(HERE.parent / "journal"))
import journal as J  # noqa: E402  (infra/journal/journal.py: the action journal, stdlib only)
sys.path.insert(0, str(HERE.parent / "lineage"))
import lineage as L  # noqa: E402  (infra/lineage/lineage.py: table → view → route → page, stdlib only)
sys.path.insert(0, str(HERE.parent / "quality"))
import claims as Q  # noqa: E402  (infra/quality/claims.py: the unsourced-claim rate, stdlib only)


# --------------------------------------------------------------------------------------- config


def _env_file_values(path: str) -> dict[str, str]:
    """Parse a KEY=value env file (the shell-sourced ~/.config/agent-ops/env)."""
    out: dict[str, str] = {}
    try:
        text = Path(path).expanduser().read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, val = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()):
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        out[key.strip()] = val
    return out


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = Path("~/.local/share/agent-mcp").expanduser()
    profile_dir: Path = Path("/srv/agent-ops/service/agent-mcp")
    projects_file: Path = HERE.parent.parent / "projects.yaml"
    people_file: Path = HERE.parent.parent / "people.yaml"
    routing_file: Path = HERE.parent.parent / "routing.yaml"
    telegram_api: str = "https://api.telegram.org"
    msg_per_hour: int = 20      # per sender; each recipient counts as one message
    msg_max_chars: int = 3500   # Telegram's limit is 4096; leave room for the header
    env_file: str = "~/.config/agent-ops/env"
    secret_globs: list[str] = field(default_factory=lambda: ["~/.config/agent-ops/*.token"])
    claude_bin: str = "claude"
    model: str = "claude-opus-5-5"
    tracker_base_url: str = ""       # TRACKER_BASE_URL (the task board, e.g. https://plane.example.com)
    tracker_workspace: str = ""      # TRACKER_WORKSPACE
    auth_ttl: float = 300.0
    run_timeout: float = 1800.0
    ticket_ttl_days: float = 14.0

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ
        c = cls()
        c.host = e.get("AGENT_MCP_HOST", c.host)
        c.port = int(e.get("AGENT_MCP_PORT", c.port))
        c.data_dir = Path(e.get("AGENT_MCP_DATA_DIR", str(c.data_dir))).expanduser()
        c.profile_dir = Path(e.get("AGENT_MCP_PROFILE_DIR", str(c.profile_dir))).expanduser()
        c.projects_file = Path(e.get("AGENT_MCP_PROJECTS", str(c.projects_file))).expanduser()
        c.people_file = Path(e.get("AGENT_MCP_PEOPLE", str(c.people_file))).expanduser()
        c.routing_file = Path(e.get("AGENT_MCP_ROUTING", str(c.routing_file))).expanduser()
        c.telegram_api = e.get("AGENT_MCP_TELEGRAM_API", c.telegram_api).rstrip("/")
        c.msg_per_hour = int(e.get("AGENT_MCP_MSG_PER_HOUR", c.msg_per_hour))
        c.env_file = e.get("AGENT_OPS_ENV", c.env_file)
        if e.get("AGENT_MCP_SECRET_GLOBS"):
            c.secret_globs = e["AGENT_MCP_SECRET_GLOBS"].split(":")
        c.claude_bin = e.get("AGENT_MCP_CLAUDE", c.claude_bin)
        c.model = e.get("AGENT_MCP_MODEL", c.model)
        file_env = _env_file_values(c.env_file)
        c.tracker_base_url = (e.get("AGENT_MCP_TRACKER_URL") or file_env.get("TRACKER_BASE_URL")
                              or e.get("TRACKER_BASE_URL") or c.tracker_base_url).rstrip("/")
        c.tracker_workspace = (e.get("AGENT_MCP_TRACKER_WORKSPACE") or file_env.get("TRACKER_WORKSPACE")
                               or e.get("TRACKER_WORKSPACE") or c.tracker_workspace)
        c.auth_ttl = float(e.get("AGENT_MCP_AUTH_TTL", c.auth_ttl))
        c.run_timeout = float(e.get("AGENT_MCP_RUN_TIMEOUT", c.run_timeout))
        c.ticket_ttl_days = float(e.get("AGENT_MCP_TICKET_TTL_DAYS", c.ticket_ttl_days))
        return c


# Effort and turn budget per `depth`.
DEPTHS: dict[str, dict[str, Any]] = {
    "quick": {"effort": "low", "max_turns": 20},
    "default": {"effort": "medium", "max_turns": 50},
    "deep": {"effort": "high", "max_turns": 120},
}


def load_projects(path: Path) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(path.read_text()) or {}
    projects = {}
    for key, p in (data.get("projects") or {}).items():
        if not p or not p.get("tracker_project_id"):
            continue
        projects[str(key).upper()] = {
            "key": str(key).upper(),
            "name": p.get("name", key),
            "tracker_project_id": str(p["tracker_project_id"]),
            "description": p.get("description", ""),
            "repos": [r for r in (p.get("repos") or []) if r and r.get("path")],
            "data_sources": list(p.get("data_sources") or []),
        }
    return projects


# ------------------------------------------------------------------------------------ team directory


class Directory:
    """people.yaml (who, how to reach them) and routing.yaml (teams per project). Re-read when the
    files change, so an edit to people.yaml needs no restart."""

    def __init__(self, people_file: Path, routing_file: Path, default_project: str = ""):
        self.files = (people_file, routing_file)
        self.default_project = default_project
        self._stamp: tuple[float, ...] = ()
        self.people: dict[str, dict[str, Any]] = {}
        self.routing: dict[str, Any] = {}

    def load(self) -> "Directory":
        stamp = tuple(f.stat().st_mtime if f.exists() else 0.0 for f in self.files)
        if stamp != self._stamp:
            people = (yaml.safe_load(self.files[0].read_text()) or {}).get("people") or {} if self.files[0].exists() else {}
            self.people = {str(k): (v or {}) for k, v in people.items()}
            self.routing = (yaml.safe_load(self.files[1].read_text()) or {}) if self.files[1].exists() else {}
            self._stamp = stamp
        return self

    def by_email(self, email: str) -> str | None:
        email = (email or "").strip().lower()
        return next((k for k, p in self.people.items() if str(p.get("email") or "").strip().lower() == email and email), None)

    def name(self, key: str) -> str:
        return str(self.people.get(key, {}).get("full_name") or key)

    def chat_id(self, key: str) -> str:
        return str(self.people.get(key, {}).get("telegram_chat_id") or "").strip()

    def teams(self, project: str) -> dict[str, list[str]]:
        """team -> people keys in one project (the default project: routing.yaml `teams`; others:
        `projects.<KEY>.teams`). The default project is routing.yaml `default_project`, else the
        first project in projects.yaml."""
        r = self.routing
        default = str(r.get("default_project") or self.default_project or "").upper()
        own = ((r.get("projects") or {}).get(project) or {}).get("teams")
        src = r.get("teams") if (project == default and not own) else own
        out: dict[str, list[str]] = {}
        for team, t in (src or {}).items():
            t = t or {}
            members = t.get("people") or ([t["person"]] if t.get("person") else [])
            out[str(team)] = [str(m) for m in members if str(m) in self.people]
        return out

    def find(self, who: str) -> list[str]:
        """People keys matching a name, key, first name, Telegram @username or email."""
        w = (who or "").strip().lstrip("@").lower()
        if not w:
            return []
        if w in self.people:
            return [w]
        hits = []
        for k, p in self.people.items():
            full = str(p.get("full_name") or "").lower()
            if w in (full, full.split(" ")[0] if full else "", str(p.get("telegram_username") or "").lower(),
                     str(p.get("email") or "").lower()):
                hits.append(k)
        return hits


# ------------------------------------------------------------------------------------ redaction


class Redactor:
    """Replaces every secret value (env file values ≥ 8 chars, token files, and parts of them
    such as the password inside a connection URL or a Basic auth header) with [redacted]."""

    MIN = 8

    def __init__(self, env_file: str, secret_globs: list[str]):
        self.env_file = env_file
        self.secret_globs = secret_globs
        self._values: list[str] = []
        self._loaded_at = 0.0

    def _collect(self) -> list[str]:
        raw: set[str] = set()
        for key, val in _env_file_values(self.env_file).items():
            raw.add(val)
        for pattern in self.secret_globs:
            for f in glob.glob(os.path.expanduser(pattern)):
                with contextlib.suppress(OSError):
                    raw.add(Path(f).read_text().strip())
        values: set[str] = set()
        for v in raw:
            values.update(self._parts(v))
        return sorted((v for v in values if len(v) >= self.MIN), key=len, reverse=True)

    @staticmethod
    def _parts(v: str) -> set[str]:
        out = {v}
        if "://" in v:
            with contextlib.suppress(ValueError):
                u = urllib.parse.urlsplit(v)
                for part in (u.username, u.password):
                    if part:
                        out.add(part)
                        out.add(urllib.parse.unquote(part))
                for _, qv in urllib.parse.parse_qsl(u.query):
                    out.add(qv)
        m = re.match(r"(?i)^(basic|bearer|token)\s+(\S+)$", v)
        if m:
            out.add(m.group(2))
            if m.group(1).lower() == "basic":
                with contextlib.suppress(Exception):
                    decoded = base64.b64decode(m.group(2) + "===").decode()
                    out.add(decoded)
                    out.update(decoded.split(":", 1))
        return out

    def values(self) -> list[str]:
        if time.monotonic() - self._loaded_at > 60 or not self._loaded_at:
            self._values = self._collect()
            self._loaded_at = time.monotonic()
        return self._values

    def __call__(self, text: Any, extra: tuple[str, ...] = ()) -> Any:
        if text is None:
            return None
        if isinstance(text, list):
            return [self(t, extra) for t in text]
        s = str(text)
        for v in sorted({*self.values(), *(e for e in extra if e and len(e) >= self.MIN)}, key=len, reverse=True):
            if v in s:
                s = s.replace(v, "[redacted]")
        for rx, repl in SECRET_SHAPES:
            s = rx.sub(repl, s)
        return s


# Secrets by shape, for values the env file doesn't know (a teammate's own token in a journal
# record or an answer). The same kinds plugins/core/scripts/guard.sh rule 10 blocks; here they are
# blanked instead. Credential URLs and auth headers keep their prefix so the text stays readable.
SECRET_SHAPES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"gl(?:pat|dt|rt|ptt|ft|imt|oas|cbt|soat|ffct|agent)-[A-Za-z0-9_-]{20,}"), "[redacted]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,}"), "[redacted]"),
    (re.compile(r"plane_api_[A-Za-z0-9]{32,}"), "[redacted]"),
    (re.compile(r"(?<![A-Za-z0-9_-])sk-(?:ant-[a-z0-9]+-|proj-)?[A-Za-z0-9_-]{32,}"), "[redacted]"),
    (re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])"), "[redacted]"),
    (re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|$)", re.S), "[redacted]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[redacted]"),
    (re.compile(r"\b[0-9]{8,10}:AA[A-Za-z0-9_-]{33}"), "[redacted]"),
    (re.compile(r"(hooks\.slack\.com/services/)[A-Za-z0-9/]{20,}"), r"\1[redacted]"),
    (re.compile(r"(/hooks/)[a-z0-9]{26}(?![a-z0-9])"), r"\1[redacted]"),
    (re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://[^/\s:@\"]+:)[^/\s@\"]{6,}@"), r"\1[redacted]@"),
    (re.compile(r"((?:[Aa]uthorization|X-API-Key|X-Api-Key|x-api-key|PRIVATE-TOKEN|Private-Token)\"?:\s*\"?"
                r"(?:[Bb]asic |[Bb]earer |[Tt]oken )?)[A-Za-z0-9+/._=-]{20,}"), r"\1[redacted]"),
]


# ----------------------------------------------------------------------------------------- auth


class AuthError(Exception):
    pass


@dataclass
class Caller:
    user_id: str
    email: str
    project_ids: frozenset[str]
    token: str


class TrackerAuth:
    """Validates a caller's task-board token through the tracker adapter (infra/adapters):
    identity from `whoami()`, project membership from `projects()` (`is_member`). Cached for `ttl`
    seconds. `make_tracker(token)` returns an adapter object."""

    def __init__(self, make_tracker, ttl: float):
        self.make_tracker = make_tracker
        self.ttl = ttl
        self._cache: dict[str, tuple[float, Caller | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _validate_sync(self, token: str) -> Caller | None:
        tr = self.make_tracker(token)
        try:
            me = tr.whoami()
        except Exception as exc:  # noqa: BLE001
            if re.search(r"\b(401|403)\b", str(exc)) or getattr(exc, "status", None) in (401, 403):
                return None
            raise
        if not me or not me.get("id"):
            return None
        ids = {str(p["id"]) for p in tr.projects() or [] if p.get("id") and p.get("is_member", True)}
        return Caller(user_id=str(me.get("id")), email=str(me.get("email", "")).lower(),
                      project_ids=frozenset(ids), token=token)

    async def validate(self, token: str | None) -> Caller:
        if not token:
            raise AuthError("missing task-board token (send Authorization: Bearer <token>)")
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and hit[0] > now:
            if hit[1] is None:
                raise AuthError("invalid task-board token")
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._cache.get(key)
            if hit and hit[0] > time.monotonic():
                if hit[1] is None:
                    raise AuthError("invalid task-board token")
                return hit[1]
            try:
                caller = await asyncio.to_thread(self._validate_sync, token)
            except Exception as exc:  # noqa: BLE001
                log.error("token validation failed: %s", type(exc).__name__)
                raise AuthError("could not validate the token with the task board right now; try again") from None
            # Bad tokens are remembered briefly so a misconfigured client can't hammer the task board.
            self._cache[key] = (time.monotonic() + (self.ttl if caller else 30), caller)
            if caller is None:
                raise AuthError("invalid task-board token")
            return caller


def token_from_headers(headers: Any) -> str | None:
    auth = headers.get("authorization") or ""
    m = re.match(r"(?i)^\s*bearer\s+(\S+)\s*$", auth)
    if m:
        return m.group(1)
    key = headers.get("x-api-key")
    return key.strip() if key else None


# -------------------------------------------------------------------------------------- storage

SCHEMA = """
create table if not exists conversations (
  id text primary key,
  owner_id text not null,
  owner_email text not null,
  project text not null,
  title text not null,
  session_id text not null,
  session_started integer not null default 0,
  status text not null default 'open',
  created_at real not null,
  updated_at real not null
);
create index if not exists conversations_owner on conversations(owner_id, project);
create table if not exists tickets (
  id text primary key,
  conversation_id text not null references conversations(id),
  owner_id text not null,
  depth text not null,
  status text not null,
  answer text,
  sources text,
  error text,
  created_at real not null,
  started_at real,
  finished_at real
);
create index if not exists tickets_conversation on tickets(conversation_id);
create table if not exists messages (
  id text primary key,
  sender text not null,
  recipient text not null,
  channel text not null,
  status text not null,
  error text,
  created_at real not null
);
create index if not exists messages_sender on messages(sender, created_at);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(path.parent, 0o700)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)

    def q(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        return self.db.execute(sql, args).fetchall()

    def one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        rows = self.q(sql, *args)
        return rows[0] if rows else None

    def recover(self, ttl_days: float) -> None:
        """Runs at start: work that was queued or running died with the old process."""
        now = time.time()
        self.q("update tickets set status='error', error='the server restarted before this ask finished; ask again',"
               " finished_at=? where status in ('queued','running')", now)
        self.q("delete from tickets where created_at < ?", now - ttl_days * 86400)
        self.q("delete from messages where created_at < ?", now - 90 * 86400)


# --------------------------------------------------------------------------------------- runner

SOURCES_RE = re.compile(r"\n#{1,6}\s*Sources\s*:?\s*\n|\n\*\*Sources:?\*\*:?\s*\n|\nSources:\s*\n", re.I)


def split_sources(text: str) -> tuple[str, list[str]]:
    matches = list(SOURCES_RE.finditer("\n" + text))
    if not matches:
        return text.strip(), []
    m = matches[-1]
    body = ("\n" + text)[: m.start()].strip()
    tail = ("\n" + text)[m.end():]
    sources = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip() for line in tail.splitlines()]
    return body, [s for s in sources if s]


def project_brief(p: dict[str, Any]) -> str:
    repos = "\n".join(f"  - {r['name']}: {r['path']}"
                      + (f" (only `{r['only']}`: always add `-- {r['only']}`, ignore the rest)" if r.get("only") else "")
                      for r in p["repos"]) or "  (none)"
    return (f"Project: {p['key']} ({p['name']}), task-board project id {p['tracker_project_id']}.\n"
            f"{p['description']}\n"
            f"Repos (read-only clones, search with `git -C <path> grep|log|show|ls-files|blame`):\n{repos}\n"
            f"Data sources that apply: {', '.join(p['data_sources']) or 'none'}.")


class Runner:
    def __init__(self, cfg: Config, store: Store, redact: Redactor, projects: dict[str, dict[str, Any]]):
        self.cfg = cfg
        self.store = store
        self.redact = redact
        self.projects = projects
        self.locks: dict[str, asyncio.Lock] = {}
        self.done: dict[str, asyncio.Event] = {}
        self.tasks: set[asyncio.Task] = set()

    def child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        for k in [k for k in env if k == "CLAUDECODE" or k.startswith("CLAUDE_CODE_") and k != "CLAUDE_CODE_OAUTH_TOKEN"]:
            env.pop(k)
        env.update(_env_file_values(self.cfg.env_file))
        # Same auth rule as infra/bin/run-agent.sh: empty values must not shadow the other one.
        for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
            if not env.get(k):
                env.pop(k, None)
        if env.get("ANTHROPIC_API_KEY"):
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        env.setdefault("MCP_TIMEOUT", "120000")
        env["MCP_TIMEOUT"] = env.get("MCP_TIMEOUT") or "120000"
        env.setdefault("AGENT_OPS_ROOT", str(self.cfg.profile_dir.resolve().parent.parent))
        return env

    def submit(self, ticket_id: str, conversation_id: str, prompt: str, depth: str, caller_token: str) -> None:
        self.done[ticket_id] = asyncio.Event()
        task = asyncio.create_task(self._run(ticket_id, conversation_id, prompt, depth, caller_token))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _run(self, ticket_id: str, conversation_id: str, prompt: str, depth: str, caller_token: str) -> None:
        lock = self.locks.setdefault(conversation_id, asyncio.Lock())
        try:
            async with lock:  # one run per conversation at a time; FIFO
                await self._run_locked(ticket_id, conversation_id, prompt, depth, caller_token)
        except Exception as exc:  # noqa: BLE001
            log.exception("ticket %s failed", ticket_id)
            self.store.q("update tickets set status='error', error=?, finished_at=? where id=?",
                         self.redact(f"internal error: {type(exc).__name__}", (caller_token,)), time.time(), ticket_id)
        finally:
            ev = self.done.pop(ticket_id, None)
            if ev:
                ev.set()

    async def _run_locked(self, ticket_id: str, conversation_id: str, prompt: str, depth: str, caller_token: str) -> None:
        conv = self.store.one("select * from conversations where id=?", conversation_id)
        if conv is None or conv["status"] != "open":
            self.store.q("update tickets set status='error', error='conversation is closed', finished_at=? where id=?",
                         time.time(), ticket_id)
            return
        started = time.time()
        if not (self.cfg.profile_dir / ".mcp.json").is_file():
            log.error("answering profile missing at %s", self.cfg.profile_dir)
            self.store.q("update tickets set status='error', error=?, finished_at=? where id=?",
                         "the answering agent is not installed on the server yet; tell your admin", started, ticket_id)
            return
        self.store.q("update tickets set status='running', started_at=? where id=?", started, ticket_id)
        d = DEPTHS.get(depth, DEPTHS["default"])
        args = [self.cfg.claude_bin, "-p", prompt,
                "--strict-mcp-config", "--mcp-config", ".mcp.json",
                "--output-format", "json",
                "--model", self.cfg.model, "--effort", d["effort"], "--max-turns", str(d["max_turns"])]
        resumed = bool(conv["session_started"])
        args += ["--resume", conv["session_id"]] if resumed else ["--session-id", conv["session_id"]]
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=str(self.cfg.profile_dir), env=self.child_env(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.cfg.run_timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            out, err = b"", b""
            rc = "timeout"
        else:
            rc = proc.returncode
        result: dict[str, Any] = {}
        with contextlib.suppress(ValueError):
            parsed = json.loads(out.decode(errors="replace") or "{}")
            result = parsed if isinstance(parsed, dict) else {}
        if result.get("session_id") and not resumed:
            # Claude created the session (even if the run then failed): follow-ups resume it.
            self.store.q("update conversations set session_started=1, session_id=? where id=?",
                         str(result["session_id"]), conversation_id)
        text = result.get("result")
        now = time.time()
        self.store.q("update conversations set updated_at=? where id=?", now, conversation_id)
        if rc == 0 and not result.get("is_error") and isinstance(text, str) and text.strip():
            body, sources = split_sources(text)
            self.store.q("update tickets set status='done', answer=?, sources=?, finished_at=? where id=?",
                         self.redact(body, (caller_token,)), json.dumps(self.redact(sources, (caller_token,))), now, ticket_id)
            status = "done"
            self.record_quality(conv["project"], depth, body, sources, now)
        else:
            reason = (result.get("subtype") or (text if isinstance(text, str) else "")
                      or (err.decode(errors="replace").strip().splitlines() or [""])[-1] or f"exit {rc}")
            msg = f"the answering agent failed ({rc}): {str(reason)[:300]}"
            self.store.q("update tickets set status='error', error=?, finished_at=? where id=?",
                         self.redact(msg, (caller_token,)), now, ticket_id)
            status = "error"
        # Timings and outcome only: never the question or the answer.
        log.info("ticket=%s conversation=%s depth=%s resumed=%s status=%s exit=%s seconds=%.1f turns=%s",
                 ticket_id, conversation_id, depth, resumed, status, rc, now - started, result.get("num_turns"))

    def record_quality(self, project: str, depth: str, body: str, sources: list, ts: float) -> None:
        """Counts only (never text): how many claims the answer makes and how many cite nothing
        inline, and how many sources it lists. One line per answer in answers-quality.jsonl, read by
        the Agent Ops Reference."""
        try:
            m = Q.measure(body, all_parts=True)
            row = {"ts": round(ts), "project": project, "depth": depth, "claims": m["claims"],
                   "unsourced": m["unsourced"], "rate": m["rate"], "sources": len(sources or [])}
            with open(self.cfg.data_dir / "answers-quality.jsonl", "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as exc:  # noqa: BLE001 - a metric must never fail an answer
            log.warning("quality not recorded: %s", type(exc).__name__)

    async def wait(self, ticket_id: str, seconds: float) -> None:
        ev = self.done.get(ticket_id)
        if ev is not None and seconds > 0:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(ev.wait(), timeout=seconds)


# ---------------------------------------------------------------------------------------- tools

INSTRUCTIONS = """\
Ask questions about a whole team project (code in every repo, warehouse and application data,
CI jobs, merge/pull requests and pipelines, task-board items) that you may not be able to see
yourself. An agent with read-only access investigates and answers in English, for you (an agent):
facts, sources (repo/path:line, SQL used, MR/PR, task) and guidance. It explains; it never
builds or changes anything, so do the work yourself with its guidance.
Flow: list_projects → create_conversation(project, title) once per topic (or reuse one from
list_conversations) → ask(conversation_id, question) → poll get_answer(ticket, wait_seconds=50)
until status is done or error. Follow-up questions go to the same conversation_id: it keeps the
context. Answers take from ~30 s (depth quick) to many minutes (deep).
Messages: list_teammates → send_message(to, text) sends a private Telegram message from the team
bot to teammates, signed with your user's name. Only send what your user asked for: show them the
exact recipients and text first and send after they say yes. One-way: replies go to your user
directly, not back to you.
"""


def build_app(cfg: Config, *, auth: TrackerAuth | None = None, store: Store | None = None,
              connector: "J.Connector | None" = None):
    """`connector` (journal.Connector) builds the tracker and code-host adapters; tests pass one
    with fake factories. By default: infra/adapters, configured by routing.yaml and the env file."""
    projects = load_projects(cfg.projects_file)
    store = store or Store(cfg.data_dir / "agent-mcp.sqlite3")
    store.recover(cfg.ticket_ttl_days)
    redact = Redactor(cfg.env_file, cfg.secret_globs)
    env = {**_env_file_values(cfg.env_file), "TRACKER_BASE_URL": cfg.tracker_base_url, "TRACKER_WORKSPACE": cfg.tracker_workspace}
    connector = connector or J.Connector(env, root=cfg.routing_file.parent)
    auth = auth or TrackerAuth(connector.tracker, cfg.auth_ttl)
    runner = Runner(cfg, store, redact, projects)
    directory = Directory(cfg.people_file, cfg.routing_file, next(iter(projects), ""))
    journal = J.Journal(cfg.data_dir / "journal.sqlite3", projects, connector.hosts)

    mcp = FastMCP(
        "agent-mcp",
        instructions=INSTRUCTIONS,
        host=cfg.host,
        port=cfg.port,
        stateless_http=True,
        json_response=True,
        # Requests arrive through a reverse proxy with public Host headers; the task-board token is
        # the access control, so the localhost-only Host check would only get in the way.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def caller(ctx: Context) -> Caller:
        request = ctx.request_context.request
        if request is None:
            raise ValueError("no HTTP request: agent-mcp only serves streamable HTTP")
        try:
            return await auth.validate(token_from_headers(request.headers))
        except AuthError as exc:
            raise ValueError(str(exc)) from None

    def project_for(c: Caller, key: str) -> dict[str, Any]:
        p = projects.get((key or "").strip().upper())
        if p is None or p["tracker_project_id"] not in c.project_ids:
            raise ValueError(f"unknown project {key!r} or you are not a member of it on the task board; see list_projects")
        return p

    def conversation_for(c: Caller, conversation_id: str) -> sqlite3.Row:
        row = store.one("select * from conversations where id=? and owner_id=?", str(conversation_id), c.user_id)
        if row is None:
            raise ValueError("conversation not found (conversations are private to the person who created them)")
        project_for(c, row["project"])  # membership may have been revoked since
        return row

    def conv_view(row: sqlite3.Row) -> dict[str, Any]:
        return {"conversation_id": row["id"], "project": row["project"], "title": row["title"],
                "status": row["status"], "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"])}

    @mcp.tool()
    async def list_projects(ctx: Context) -> list[dict[str, Any]]:
        """Projects you can ask about (you must be a member of the project on the task board)."""
        c = await caller(ctx)
        return [{"project": p["key"], "name": p["name"], "description": p["description"],
                 "repos": [r["name"] for r in p["repos"]], "data_sources": p["data_sources"]}
                for p in projects.values() if p["tracker_project_id"] in c.project_ids]

    @mcp.tool()
    async def create_conversation(project: str, title: str, ctx: Context) -> dict[str, Any]:
        """Start a private conversation about one topic in a project. Returns its conversation_id;
        keep it and reuse it for follow-up questions on the same topic."""
        c = await caller(ctx)
        p = project_for(c, project)
        title = (title or "").strip()[:200] or "untitled"
        now = time.time()
        cid = str(uuid.uuid4())
        store.q("insert into conversations (id, owner_id, owner_email, project, title, session_id, created_at, updated_at)"
                " values (?,?,?,?,?,?,?,?)", cid, c.user_id, c.email, p["key"], title, str(uuid.uuid4()), now, now)
        return conv_view(store.one("select * from conversations where id=?", cid))

    @mcp.tool()
    async def list_conversations(ctx: Context, project: str | None = None, include_closed: bool = False) -> list[dict[str, Any]]:
        """Your conversations (newest activity first), optionally for one project."""
        c = await caller(ctx)
        rows = store.q("select * from conversations where owner_id=? order by updated_at desc", c.user_id)
        key = project.strip().upper() if project else None
        if key:
            project_for(c, key)
        return [conv_view(r) for r in rows
                if (not key or r["project"] == key) and (include_closed or r["status"] == "open")
                and r["project"] in projects and projects[r["project"]]["tracker_project_id"] in c.project_ids]

    @mcp.tool()
    async def rename_conversation(conversation_id: str, title: str, ctx: Context) -> dict[str, Any]:
        """Rename one of your conversations."""
        c = await caller(ctx)
        conversation_for(c, conversation_id)
        store.q("update conversations set title=?, updated_at=? where id=?",
                (title or "").strip()[:200] or "untitled", time.time(), conversation_id)
        return conv_view(store.one("select * from conversations where id=?", conversation_id))

    @mcp.tool()
    async def close_conversation(conversation_id: str, ctx: Context) -> dict[str, Any]:
        """Close one of your conversations (no more questions; it stays listed with include_closed)."""
        c = await caller(ctx)
        conversation_for(c, conversation_id)
        store.q("update conversations set status='closed', updated_at=? where id=?", time.time(), conversation_id)
        return conv_view(store.one("select * from conversations where id=?", conversation_id))

    @mcp.tool()
    async def ask(conversation_id: str, question: str, ctx: Context, depth: str = "default") -> dict[str, Any]:
        """Ask a question in a conversation. Returns a ticket at once; poll get_answer(ticket).
        depth: quick (small factual lookups), default, or deep (broad investigations).
        Questions in the same conversation run one after another and share context."""
        c = await caller(ctx)
        conv = conversation_for(c, conversation_id)
        if conv["status"] != "open":
            raise ValueError("this conversation is closed; create a new one")
        question = (question or "").strip()
        if not question:
            raise ValueError("question is empty")
        depth = depth if depth in DEPTHS else "default"
        p = projects[conv["project"]]
        prompt = (f"{project_brief(p)}\n\n"
                  f"Question from a teammate's agent (depth: {depth}):\n{question}")
        tid = str(uuid.uuid4())
        store.q("insert into tickets (id, conversation_id, owner_id, depth, status, created_at) values (?,?,?,?,?,?)",
                tid, conv["id"], c.user_id, depth, "queued", time.time())
        runner.submit(tid, conv["id"], prompt, depth, c.token)
        return {"ticket": tid, "conversation_id": conv["id"], "status": "queued",
                "next": "call get_answer(ticket, wait_seconds=50) until status is done or error"}

    @mcp.tool()
    async def get_answer(ticket: str, ctx: Context, wait_seconds: int = 30) -> dict[str, Any]:
        """Status of an ask. Waits up to wait_seconds (max 50) for it to finish.
        status: queued | running | done | error. When done: answer and sources."""
        c = await caller(ctx)
        row = store.one("select * from tickets where id=? and owner_id=?", str(ticket), c.user_id)
        if row is None:
            raise ValueError("ticket not found")
        conversation_for(c, row["conversation_id"])
        if row["status"] in ("queued", "running"):
            await runner.wait(row["id"], max(0, min(int(wait_seconds or 0), 50)))
            row = store.one("select * from tickets where id=?", row["id"])
        out: dict[str, Any] = {"ticket": row["id"], "conversation_id": row["conversation_id"], "status": row["status"]}
        if row["status"] == "done":
            out["answer"] = row["answer"]
            out["sources"] = json.loads(row["sources"] or "[]")
        elif row["status"] == "error":
            out["error"] = row["error"]
        else:
            out["elapsed_seconds"] = round(time.time() - (row["started_at"] or row["created_at"]))
        return out

    def sender_of(c: Caller) -> str:
        key = directory.load().by_email(c.email)
        if key is None:
            raise ValueError("your task-board email is not in the team directory (people.yaml); ask your admin to add you")
        return key

    def member_projects(c: Caller) -> list[str]:
        return [k for k, p in projects.items() if p["tracker_project_id"] in c.project_ids]

    @mcp.tool()
    async def list_teammates(ctx: Context, project: str | None = None) -> list[dict[str, Any]]:
        """Teammates you can message: key, name, role, their teams in your projects, and whether
        Telegram reaches them. Optionally only the people in one project."""
        c = await caller(ctx)
        me = sender_of(c)
        d = directory
        keys = [project_for(c, project)["key"]] if project else member_projects(c)
        teams = {k: d.teams(k) for k in keys}
        out = []
        for key, person in d.people.items():
            mine = sorted(f"{pk}:{t}" for pk, ts in teams.items() for t, ppl in ts.items() if key in ppl)
            if project and not mine:
                continue
            out.append({"key": key, "name": d.name(key), "role": str(person.get("role") or ""),
                        "teams": mine, "you": key == me,
                        "reachable": {"telegram": bool(d.chat_id(key)), "mattermost": False}})
        return out

    @mcp.tool()
    async def send_message(to: list[str] | str, text: str, ctx: Context, project: str | None = None,
                           channel: str = "auto") -> dict[str, Any]:
        """Send a private message to teammates, from the team's Telegram bot, signed with your
        user's name. ONLY when your user asked for it: show them the exact recipients and text and
        send after they confirm. `to`: names or keys of people ("Noor", "kenji"), or teams written
        "team:<name>" ("team:backend") together with `project` (a project you're a member of).
        channel: auto | telegram (Mattermost is not set up yet). One-way: the message tells the
        recipient to reply to your user directly. Limits: 3500 characters, 20 messages per hour."""
        c = await caller(ctx)
        me = sender_of(c)
        d = directory
        channel = (channel or "auto").strip().lower()
        if channel == "mattermost":
            raise ValueError("Mattermost messages are not set up yet; use channel auto or telegram")
        if channel not in ("auto", "telegram"):
            raise ValueError("channel must be auto or telegram")
        body = (text or "").strip()
        if not body:
            raise ValueError("text is empty")
        if len(body) > cfg.msg_max_chars:
            raise ValueError(f"text is {len(body)} characters; the limit is {cfg.msg_max_chars}")
        if isinstance(to, str):
            to = [to]
        recipients: list[str] = []
        team_note: list[str] = []
        for entry in to or []:
            e = str(entry).strip()
            if e.lower().startswith("team:"):
                if not project:
                    raise ValueError(f"{e}: say which project's team (project={', '.join(member_projects(c)) or 'a project key'})")
                pk = project_for(c, project)["key"]
                team = e.split(":", 1)[1].strip().lower()
                members = d.teams(pk).get(team)
                if members is None:
                    raise ValueError(f"{pk} has no team {team!r}; teams: {', '.join(sorted(d.teams(pk))) or 'none'}")
                team_note.append(f"{team} team ({pk})")
                recipients += [m for m in members if m != me]
                continue
            hits = d.find(e)
            if len(hits) != 1:
                raise ValueError(f"{e!r}: " + ("no such teammate" if not hits else "matches " + ", ".join(d.name(h) for h in hits))
                                 + "; see list_teammates")
            if hits[0] == me:
                raise ValueError("you can't send a message to yourself")
            recipients.append(hits[0])
        recipients = list(dict.fromkeys(recipients))
        if not recipients:
            raise ValueError("no recipients (a team with only you in it has nobody to message)")
        unreachable = [d.name(r) for r in recipients if not d.chat_id(r)]
        if unreachable:
            raise ValueError("not reachable on Telegram yet (they haven't started a chat with the team bot): "
                             + ", ".join(unreachable) + ". Nothing was sent; send to the others without them.")
        hour_ago = time.time() - 3600
        sent_last_hour = store.one("select count(*) n from messages where sender=? and created_at>? and status='sent'",
                                   me, hour_ago)["n"]
        if sent_last_hour + len(recipients) > cfg.msg_per_hour:
            raise ValueError(f"limit is {cfg.msg_per_hour} messages per hour; you've sent {sent_last_hour}. Nothing was sent.")
        token = _env_file_values(cfg.env_file).get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise ValueError("Telegram is not configured on the server (no TELEGRAM_BOT_TOKEN); tell your admin")
        sender = d.people.get(me, {})
        handle = str(sender.get("telegram_username") or "").strip()
        lines = [f"💬 <b>From {html.escape(d.name(me))}</b> (via Claude)"
                 + (f" to the {html.escape(', '.join(team_note))}" if team_note else ""), "",
                 html.escape(redact(body, (c.token,))), "",
                 f"<i>Reply to {html.escape(d.name(me).split(' ')[0])} directly"
                 + (f": @{html.escape(handle)}" if handle else "") + " (this bot doesn't pass replies on).</i>"]
        message = "\n".join(lines)
        sent, failed = [], []
        for r in recipients:
            mid = str(uuid.uuid4())
            try:
                await asyncio.to_thread(telegram_send, cfg.telegram_api, token, d.chat_id(r), message)
                status, err = "sent", None
                sent.append(d.name(r))
            except Exception as exc:  # noqa: BLE001
                status, err = "error", redact(str(exc)[:200], (token,))
                failed.append({"name": d.name(r), "error": err})
            store.q("insert into messages (id, sender, recipient, channel, status, error, created_at) values (?,?,?,?,?,?,?)",
                    mid, me, r, "telegram", status, err, time.time())
            log.info("message id=%s from=%s to=%s channel=telegram status=%s", mid, me, r, status)
        return {"sent": sent, "failed": failed, "channel": "telegram",
                "note": "one-way: replies go to your user directly, not back to you"}

    # ------------------------------------------------------------------------- action journal
    def actor_of(c: Caller, headers: dict[str, str], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """A person in people.yaml is always that person's agent (whatever the headers say); a token
        that isn't a person (the service agents' bot users) is a service agent."""
        extra = extra or {}
        h = lambda k: (extra.get(k) if extra.get(k) is not None else headers.get(f"x-agent-{k}", "")) or ""
        person = directory.load().by_email(c.email)
        return {"kind": "person-agent" if person else "service-agent", "person": person, "user_id": c.user_id,
                "email": c.email, "role": h("role")[:32] or None, "agent": None if person else (h("name") or h("agent") or c.email)[:64],
                "run_id": None if person else (h("run")[:64] or None), "client": h("client")[:32] or None}

    def ingest(c: Caller, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        phase = (headers.get("x-journal-phase") or "post").lower()
        tracker = connector.tracker(c.token)
        codehosts = connector.read_codehosts()
        red = lambda s: redact(s, (c.token,))
        if phase == "batch":
            n = 0
            for line in body.decode("utf-8", "replace").splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict) and isinstance(item.get("payload"), dict):
                    journal.ingest("post", item["payload"], actor_of(c, headers, item), tracker, codehosts, red)
                    n += 1
            return {"ok": True, "ingested": n}
        payload = json.loads(body or b"{}")
        if not isinstance(payload, dict) or phase not in ("pre", "post"):
            raise ValueError("expected a hook payload (JSON object) and X-Journal-Phase pre|post|batch")
        return {"ok": True, "event": journal.ingest(phase, payload, actor_of(c, headers), tracker, codehosts, red)}

    def is_approver(person: str | None) -> bool:
        r = directory.load().routing
        return bool(person) and person in set(r.get("approvers") or []) | {r.get("question_owner")}

    def visible(c: Caller, e: dict[str, Any]) -> bool:
        me = directory.load().by_email(c.email)
        return (e.get("project") in member_projects(c) or e.get("user_id") == c.user_id or is_approver(me))

    @mcp.tool()
    async def blame(target: str, ctx: Context, since_days: float = 90) -> list[dict[str, Any]]:
        """Who changed `target` and how: a task key (BIL-123), an MR/PR (group/repo!12) or another
        journal target. Each row: when, who (a person, their agent, a service-agent run, a script),
        how, what changed, and how to undo it. For tasks, changes made by hand (no agent recorded)
        come from the item's activity on the task board."""
        c = await caller(ctx)
        tracker = connector.tracker(c.token)
        rows = await asyncio.to_thread(journal.blame, target.strip(), time.time() - since_days * 86400, tracker)
        evs = {e["id"]: e for e in journal.events(target=target.strip(), limit=500)}
        return [r for r in rows if r.get("event") is None or visible(c, evs.get(r["event"], {}))]

    @mcp.tool()
    async def suspects(project: str, term: str, ctx: Context, since_days: float = 7) -> list[dict[str, Any]]:
        """Which recent changes may have broken something: give the broken table, file, route, page
        or metric name. Returns commits in the project's repos that add/remove that text or touch
        such a path (author, when, agent-assisted or not, on the default branch or not) and journal
        events that mention it, most likely first."""
        c = await caller(ctx)
        p = project_for(c, project)

        def run() -> list[dict[str, Any]]:
            try:
                up = L.upstream_terms(L.load(), term)
            except Exception:  # noqa: BLE001 - suspects still works without lineage
                up = []
            all_repos = [r for k in member_projects(c) for r in projects[k]["repos"]]
            return J.suspects(term, p["repos"], since_days, journal, upstream=up, upstream_repos=all_repos)

        rows = await asyncio.to_thread(run)
        return [r for r in rows if r["kind"] != "journal" or visible(c, (journal.events(event=r["event"]) or [{}])[0])]

    @mcp.tool()
    async def lineage(node: str, ctx: Context, direction: str = "impact") -> dict[str, Any]:
        """Where data comes from and where it goes: give a table or view (`analytics.orders`,
        `v_revenue_daily`), an API route (`GET /invoices/summary` or its path), a page
        (`/revenue`), a metric (`metric:mrr`) or a repo file path. `direction`: `impact`
        (everything downstream: views, routes, pages, metrics that change if it changes) or
        `upstream` (everything it is built from: views, tables, pipeline jobs, code files). Scanned
        from the repos and the warehouse catalog, refreshed daily."""
        await caller(ctx)
        g = await asyncio.to_thread(L.load)
        starts = g.resolve(node)
        if not starts:
            raise ValueError(f"nothing matches {node!r}: try a table (db.table / v_name), a route path, a page path or metric:<id>")
        rows = g.walk(starts, "down" if direction != "upstream" else "up")
        return {"matched": starts, "direction": "upstream" if direction == "upstream" else "impact",
                "summary": g.summary([r for r in rows if r["node"] not in starts]),
                "owners": sorted({r.get("owner") for r in rows if r.get("owner")})}

    @mcp.tool()
    async def revert_plan(ctx: Context, event: str | None = None, run: str | None = None, session: str | None = None,
                          target: str | None = None, actor: str | None = None, since_hours: float | None = None) -> dict[str, Any]:
        """Dry run: what undoing these changes would do. Select by journal `event` id, service-agent
        `run`, Claude `session`, or `target` (BIL-123) and/or `actor` within `since_hours`. Nothing
        changes now: the plan is saved and an approver applies it on the agent host with
        `infra/bin/journal revert-apply <plan>`. Steps marked manual must be done by hand."""
        c = await caller(ctx)
        sel = {"event": event, "run": run, "session": session, "target": target, "actor": actor,
               "since": time.time() - since_hours * 3600 if since_hours else None}
        me = directory.load().by_email(c.email)
        plan = journal.plan(sel, created_by=me or c.email)
        hidden = [s for s in plan["steps"] if not visible(c, (journal.events(event=s["event"]) or [{}])[0])]
        if hidden:
            raise ValueError("some of these changes are in projects you are not a member of; ask an approver")
        return plan

    app = AuthGate(mcp.streamable_http_app(), auth, ingest)
    app.state_ = {"mcp": mcp, "runner": runner, "store": store, "auth": auth, "redact": redact, "directory": directory,
                  "journal": journal, "connector": connector}  # for tests
    return app


def telegram_send(api: str, token: str, chat_id: str, text: str) -> None:
    data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(f"{api}/bot{token}/sendMessage", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - fixed Telegram API URL
            body = json.load(r)
    except urllib.error.HTTPError as exc:
        try:
            desc = json.load(exc).get("description", "")
        except Exception:  # noqa: BLE001
            desc = ""
        raise RuntimeError(f"Telegram HTTP {exc.code} {desc}".strip()) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram unreachable: {type(exc.reason).__name__}") from None
    if not body.get("ok"):
        raise RuntimeError(f"Telegram: {body.get('description', 'not ok')}")


def _iso(ts: float | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None


class AuthGate:
    """ASGI front door: /healthz is open; everything else needs a valid task-board token (401).
    POST /journal and /journal/batch (the journal.sh hook) are answered here, not by MCP."""

    MAX_BODY = 4_000_000

    def __init__(self, app, auth: TrackerAuth, journal_ingest=None):
        self.app = app
        self.auth = auth
        self.journal_ingest = journal_ingest

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope.get("path") == "/healthz":
            return await _respond(send, 200, {"ok": True})
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        try:
            c = await self.auth.validate(token_from_headers(headers))
        except AuthError as exc:
            return await _respond(send, 401, {"error": str(exc)})
        if scope.get("path") in ("/journal", "/journal/batch") and self.journal_ingest:
            if scope.get("method") != "POST":
                return await _respond(send, 405, {"error": "POST only"})
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if len(body) > self.MAX_BODY:
                    return await _respond(send, 413, {"error": "too large"})
                if not msg.get("more_body"):
                    break
            if scope["path"] == "/journal/batch":
                headers["x-journal-phase"] = "batch"
            try:
                out = await asyncio.to_thread(self.journal_ingest, c, headers, body)
            except Exception as exc:  # noqa: BLE001 - the hook ignores errors; log them here
                log.warning("journal ingest failed: %s", type(exc).__name__)
                return await _respond(send, 400, {"error": type(exc).__name__})
            return await _respond(send, 200, out)
        return await self.app(scope, receive, send)


async def _respond(send, status: int, body: dict) -> None:
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Keep request lines (which carry no content) but nothing from the MCP payloads.
    logging.getLogger("mcp").setLevel(logging.WARNING)
    cfg = Config.from_env()
    # The answering agent's .mcp.json is rendered from its template and routing.yaml (not committed).
    render = Path(__file__).resolve().parents[1] / "bin" / "render-mcp.py"
    if render.is_file():
        r = subprocess.run([sys.executable, "-I", str(render), "agent-mcp"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            log.warning("render-mcp.py agent-mcp failed: %s", (r.stderr or r.stdout).strip()[:300])
    if not (cfg.profile_dir / ".mcp.json").is_file():
        log.warning("profile dir %s has no .mcp.json", cfg.profile_dir)
    app = build_app(cfg)
    import uvicorn

    if not cfg.tracker_base_url:
        log.warning("TRACKER_BASE_URL is not set: callers can't be authenticated")
    log.info("listening on http://%s:%s/mcp (profile %s, task board %s)", cfg.host, cfg.port, cfg.profile_dir, cfg.tracker_base_url)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning", lifespan="on")


if __name__ == "__main__":
    main()
