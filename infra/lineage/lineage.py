#!/usr/bin/env python3
"""Lineage: which warehouse table feeds which view, API route, page and metric, and back.

The graph is built from what exists, never drawn by hand. *Sources* say where to look; each has a
`type` handled by a scanner (built in below, or a module in `infra/lineage/scanners/`):
- `sql_dir`           a folder of pipeline SQL: the table each file builds and the tables it reads;
- `clickhouse_views`  views from ClickHouse's own catalog (system.tables, read-only user);
- `elysia_routes`     an Elysia (TypeScript) backend: router prefix + method path → controller →
                      services → repository → the warehouse tables/views its SQL reads;
- `react_router`      a React Router frontend: route paths → page folder → API path literals →
                      the backend route they call.
Sources live in `knowledge/lineage-sources.yaml` (example: `infra/lineage/sources.example.yaml`).
By hand, `knowledge/lineage.yaml` adds what code can't tell: metrics, page names and owners. Every
name there is checked against the generated graph (`lineage check`).

  lineage build                       scan and save the graph (~/.local/share/agent-ops/lineage.json)
  lineage impact <node>               everything downstream: what breaks if <node> changes
  lineage upstream <node>             everything it is built from: where a wrong number can come from
  lineage files <path> [<path>…]      the nodes defined in these repo files (an MR's changed files)
  lineage check                       hand-file names that no longer exist; nodes nobody owns
  lineage sources                     the sources in use and which scanner types exist
<node>: a table (`analytics.orders`, or just `orders`), a route (`GET /invoices/summary` or the
path alone), a page (`/revenue`), a metric (`metric:mrr`) or a file path.

Adding a scanner (e.g. Express, FastAPI, dbt): drop `infra/lineage/scanners/<name>.py` defining
`TYPE = "<name>"`, `STAGE = 0|1|2` (0 data, 1 API routes, 2 pages: later stages see earlier nodes)
and `def scan(g, cfg, ctx)`, which adds nodes and edges with `g.node(...)` / `g.edge(...)` using the
node names above (`table:<db>.<name>`, `route:<METHOD> <path>`, `page:<path>`, `file:<repo>/<path>`,
`job:<source>:<name>`). `cfg` is the source's settings; `ctx` has `repos` (Path), `env`, `notes`.
Standard library only (runs on the host's python3 and inside agent-mcp).
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(os.environ.get("AGENT_LINEAGE_CACHE", "~/.local/share/agent-ops/lineage.json")).expanduser()
REPOS = Path(os.environ.get("AGENT_REPOS_DIR", "/srv/repos"))
MAX_AGE = 24 * 3600

# --------------------------------------------------------------------------------------- graph


class Graph:
    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.edges: Set[Tuple[str, str, str]] = set()        # (src, dst, kind): data flows src → dst
        self.evidence: Dict[Tuple[str, str], str] = {}

    def node(self, nid: str, **attrs) -> str:
        n = self.nodes.setdefault(nid, {"id": nid})
        for k, v in attrs.items():
            if v is not None and k not in n:
                n[k] = v
        return nid

    def edge(self, src: str, dst: str, kind: str, evidence: str = "") -> None:
        if src == dst:
            return
        self.edges.add((src, dst, kind))
        if evidence and (src, dst) not in self.evidence:
            self.evidence[(src, dst)] = evidence

    def to_json(self) -> Dict[str, Any]:
        return {"built_at": int(time.time()), "nodes": list(self.nodes.values()),
                "edges": [{"from": s, "to": d, "kind": k, "evidence": self.evidence.get((s, d), "")}
                          for s, d, k in sorted(self.edges)]}

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Graph":
        g = cls()
        for n in data.get("nodes", []):
            g.nodes[n["id"]] = n
        for e in data.get("edges", []):
            g.edges.add((e["from"], e["to"], e["kind"]))
            if e.get("evidence"):
                g.evidence[(e["from"], e["to"])] = e["evidence"]
        g.built_at = data.get("built_at")
        return g

    # ---- queries ------------------------------------------------------------------------------
    def resolve(self, name: str) -> List[str]:
        """Node ids matching a loose name: exact id, `kind:name`, a table with or without its
        database, a route with or without the method, a path's last part."""
        s = name.strip()
        if s in self.nodes:
            return [s]
        cands = [f"table:{s}", f"metric:{s}", f"page:{s}", f"route:{s}", f"file:{s}", f"job:{s}"]
        hit = [c for c in cands if c in self.nodes]
        if hit:
            return hit
        low = s.lower()
        out = []
        for nid, n in self.nodes.items():
            kind, _, val = nid.partition(":")
            v = val.lower()
            if kind == "table" and (v == low or v.split(".", 1)[-1] == low):
                out.append(nid)
            elif kind == "route" and (v == low or v.split(" ", 1)[-1] == low):
                out.append(nid)
            elif kind in ("page", "metric") and (v == low or (n.get("name") or "").lower() == low):
                out.append(nid)
            elif kind == "file" and (v.endswith(low) and ("/" in low or "." in low)):
                out.append(nid)
        return sorted(out)

    def walk(self, starts: Iterable[str], direction: str = "down", max_depth: int = 12) -> List[Dict[str, Any]]:
        nxt = defaultdict(list)
        for s, d, k in self.edges:
            if direction == "down":
                nxt[s].append((d, k))
            else:
                nxt[d].append((s, k))
        seen: Dict[str, Dict[str, Any]] = {}
        q = deque((s, 0, None) for s in starts)
        while q:
            cur, depth, via = q.popleft()
            if cur in seen or depth > max_depth:
                continue
            seen[cur] = {"node": cur, "depth": depth, "via": via, **{k: v for k, v in self.nodes.get(cur, {}).items() if k != "id"}}
            for n, k in nxt.get(cur, []):
                if n not in seen:
                    q.append((n, depth + 1, cur))
        return sorted(seen.values(), key=lambda r: (r["depth"], r["node"]))

    def summary(self, rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = defaultdict(list)
        for r in rows:
            kind = r["node"].split(":", 1)[0]
            out[kind].append(r["node"].split(":", 1)[1] + (f" ({r['name']})" if r.get("name") else ""))
        return dict(out)


# ------------------------------------------------------------------------------- source helpers

def read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def files(repo: Path, sub: str, pattern: str) -> List[Path]:
    base = repo / sub
    return sorted(p for p in base.rglob(pattern) if p.is_file()) if base.is_dir() else []


def rel(repo: Path, p: Path) -> str:
    return f"{repo.name}/{p.relative_to(repo)}"


def lineno(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


TABLE_REF = re.compile(r"\b(?:from|join)\s+`?([a-z_][a-z0-9_]*)`?\.`?([a-z_][a-z0-9_]*)`?", re.I)
NOT_TABLES = {"system", "information_schema"}       # CTEs have no database, so they never match
STAGE = re.compile(r"_stage(_[a-z0-9]+)?$")


def prod_name(t: str, strip: "re.Pattern[str]" = STAGE) -> str:
    """db.x_stage → db.x: pipelines that build a `_stage` copy and swap it in count as building x."""
    return strip.sub("", t.lower())


def sql_sources(sql: str, strip: "re.Pattern[str]" = STAGE) -> Set[str]:
    out = set()
    for m in TABLE_REF.finditer(strip_sql_comments(sql)):
        if m.group(1).lower() not in NOT_TABLES:
            out.add(prod_name(f"{m.group(1)}.{m.group(2)}", strip))
    return out


# ------------------------------------------------------------------------------------ scanners

SCANNERS: Dict[str, Tuple[int, Callable[..., None]]] = {}     # type → (stage, scan)


def scanner(name: str, stage: int):
    def deco(fn):
        SCANNERS[name] = (stage, fn)
        return fn
    return deco


def repo_path(cfg: Dict[str, Any], ctx: Dict[str, Any]) -> Path:
    r = str(cfg.get("repo") or "")
    p = Path(r).expanduser()
    return p if p.is_absolute() else Path(ctx["repos"]) / r


def as_list(v: Any) -> List[str]:
    if v is None or v == "":
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


CREATE_RE = re.compile(r"\b(?:create\s+(?:or\s+replace\s+)?(?:table|view|materialized\s+view)(?:\s+if\s+not\s+exists)?|insert\s+into)\s+`?([a-z_][a-z0-9_]*)`?\.`?([a-z_][a-z0-9_]*)`?", re.I)


@scanner("sql_dir", 0)
def scan_sql_dir(g: Graph, cfg: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    """A folder of pipeline SQL. Each file builds the table(s) it creates or inserts into; a bare
    SELECT file builds `<default_db>.<file name>`. Sources are every db-qualified FROM/JOIN.
    Settings: `repo`, `path` (default `.`), `glob` (default `**/*.sql`), `default_db` (default
    `analytics`), `skip` (file-name globs, e.g. [zDelete_*, *_old]), `name` (job node prefix,
    default the repo name), `strip_suffix` (regex: names that count as the same table, default
    `_stage(_x)?`)."""
    repo = repo_path(cfg, ctx)
    base = repo / str(cfg.get("path") or ".")
    if not base.is_dir():
        ctx["notes"].append(f"sql_dir {cfg.get('repo')}/{cfg.get('path') or '.'}: folder not found")
        return
    strip = re.compile(str(cfg["strip_suffix"])) if cfg.get("strip_suffix") else STAGE
    skips = as_list(cfg.get("skip"))
    default_db = str(cfg.get("default_db") or "analytics")
    name = str(cfg.get("name") or repo.name)
    for p in sorted(base.glob(str(cfg.get("glob") or "**/*.sql"))):
        if not p.is_file() or any(fnmatch_ci(p.stem, s) or fnmatch_ci(p.name, s) for s in skips):
            continue
        r = rel(repo, p)
        text = strip_sql_comments(read(p))
        targets = {prod_name(f"{d}.{t}", strip) for d, t in CREATE_RE.findall(text)}
        if not targets:
            targets = {f"{default_db}.{p.stem}".lower()}
        srcs = sql_sources(text, strip) - targets
        job = g.node(f"job:{name}:{p.stem}", file=r)
        for t in targets:
            g.node(f"table:{t}", file=r, layer="warehouse")
            g.edge(job, f"table:{t}", "builds", r)
            for s in srcs:
                g.node(f"table:{s}", layer="warehouse")
                g.edge(f"table:{s}", f"table:{t}", "feeds", r)


def fnmatch_ci(name: str, pattern: str) -> bool:
    return re.fullmatch(re.escape(pattern.lower()).replace(r"\*", ".*").replace(r"\?", "."), name.lower()) is not None


def clickhouse_views(env: Dict[str, str], databases: List[str]) -> List[Dict[str, str]]:
    """Views (and materialized views) and their SELECT, from system.tables, read-only user
    (CH_RO_HOST / CH_RO_PORT / CH_RO_SECURE / CH_RO_USER / CH_RO_PASSWORD)."""
    host = env.get("CH_RO_HOST")
    if not host or not env.get("CH_RO_PASSWORD") or not databases:
        return []
    port = env.get("CH_RO_PORT", "8443")
    scheme = "https" if env.get("CH_RO_SECURE", "true").lower() == "true" else "http"
    dbs = ", ".join("'" + re.sub(r"[^A-Za-z0-9_]", "", d) + "'" for d in databases)
    q = ("-- agent: lineage (agent-ops)\n"
         "select database, name, engine, as_select from system.tables "
         f"where database in ({dbs}) and engine in ('View', 'MaterializedView') "
         "format JSON")
    url = f"{scheme}://{host}:{port}/?readonly=1&max_execution_time=30"
    auth = base64.b64encode(f"{env.get('CH_RO_USER', 'agent_ro')}:{env['CH_RO_PASSWORD']}".encode()).decode()
    req = urllib.request.Request(url, data=q.encode(), headers={"Authorization": f"Basic {auth}"})
    sslctx = ssl.create_default_context()
    if env.get("CH_RO_INSECURE") == "1":
        sslctx.check_hostname, sslctx.verify_mode = False, ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=40, context=sslctx) as r:  # noqa: S310 (your warehouse)
        return json.loads(r.read()).get("data", [])


@scanner("clickhouse_views", 0)
def scan_clickhouse_views(g: Graph, cfg: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    """ClickHouse views: each view is fed by the tables its SELECT reads. Settings: `databases`
    (list). Views that live only in the warehouse (not in any repo) are covered this way."""
    views = ctx.get("views")
    if views is None:
        try:
            views = clickhouse_views(ctx["env"], as_list(cfg.get("databases")))
        except Exception as e:  # noqa: BLE001 - the rest of the graph is still useful
            ctx["notes"].append(f"ClickHouse views not read: {type(e).__name__}")
            views = []
    for v in views:
        name = f"{v['database']}.{v['name']}".lower()
        g.node(f"table:{name}", layer="view" if v.get("engine") == "View" else "warehouse", engine=v.get("engine"))
        for s in sql_sources(v.get("as_select") or "") - {name}:
            g.node(f"table:{s}", layer="warehouse")
            g.edge(f"table:{s}", f"table:{name}", "feeds", "ClickHouse system.tables")


# Elysia backend --------------------------------------------------------------------------------

CLASS_RE = re.compile(r"^export\s+(?:default\s+)?class\s+(\w+)", re.M)
FIELD_RE = re.compile(r"(?:private|public|protected)?\s*(?:readonly\s+)?(\w+)\s*=\s*new\s+([A-Z]\w+)\(")
METHOD_RE = re.compile(r"^\t(?:(?:public|private|protected|static|async|override)\s+)*(\w+)\s*(?:<[^>\n]*>)?\s*\(", re.M)


class Klass:
    def __init__(self, name: str, path: str, text: str, view_re: "re.Pattern[str]", const_re: "re.Pattern[str]"):
        self.name, self.path = name, path
        self.fields = {f: c for f, c in FIELD_RE.findall(text)}
        self.methods: Dict[str, str] = {}
        heads = [(m.group(1), m.start()) for m in METHOD_RE.finditer(text)
                 if m.group(1) not in ("if", "for", "while", "switch", "catch", "return", "constructor")]
        for i, (name_, start) in enumerate(heads):
            end = heads[i + 1][1] if i + 1 < len(heads) else len(text)
            self.methods[name_] = text[start:end]
        consts = {c: f"{d}.{v}" for c, d, v in const_re.findall(text)}
        ok = lambda v: len(v.split(".", 1)[-1]) > 2 and not v.endswith("_")
        self.views_by_method: Dict[str, Set[str]] = {}
        for mname, body in self.methods.items():
            vs = {f"{d}.{v}" for d, v in view_re.findall(body) if ok(f"{d}.{v}")}
            vs |= {consts[c] for c in re.findall(r"\$\{(\w+)\}", body) if c in consts}
            vs |= {consts[c] for c in consts if re.search(rf"\b{c}\b", body)}
            self.views_by_method[mname] = vs
        self.all_views = {v for vs in self.views_by_method.values() for v in vs} or \
            {f"{d}.{v}" for d, v in view_re.findall(text) if ok(f"{d}.{v}")}


@scanner("elysia_routes", 1)
def scan_elysia_routes(g: Graph, cfg: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    """An Elysia backend (Bun/TypeScript): `new Elysia({ prefix })` routers with `.get("/path", …)`
    handlers calling `XController.getInstance().method()`; controllers and services hold their
    collaborators as `private readonly x = new XService()`; repositories (classes named
    `*Repository` or under `repositories/`) hold SQL naming `<db>.<table>`. Settings: `repo`,
    `src` (default `src`), `routers` (default `src/routers`), `databases` (which `<db>.` prefixes in
    SQL are warehouse tables, e.g. [analytics])."""
    repo = repo_path(cfg, ctx)
    dbs = [re.escape(d) for d in as_list(cfg.get("databases"))] or [r"[a-z_][a-z0-9_]*"]
    view_re = re.compile(rf"\b({'|'.join(dbs)})\.([a-z_][a-z0-9_]*)\b")
    const_re = re.compile(rf"\bconst\s+(\w+)\s*=\s*[`\"']\s*({'|'.join(dbs)})\.([a-z_][a-z0-9_]*)")
    classes: Dict[str, Klass] = {}
    for p in files(repo, str(cfg.get("src") or "src"), "*.ts"):
        text = read(p)
        for name in CLASS_RE.findall(text):
            classes[name] = Klass(name, rel(repo, p), text, view_re, const_re)
    if not classes:
        ctx["notes"].append(f"elysia_routes {cfg.get('repo')}: no TypeScript classes found")

    def reach(cls: str, method: Optional[str], seen: Set[Tuple[str, Optional[str]]]) -> Set[Tuple[str, str]]:
        """(repository class, table) pairs a class method reaches through this.<field>.<method>()
        and this.<method>() calls; method None = the whole class."""
        if (cls, method) in seen or cls not in classes:
            return set()
        seen.add((cls, method))
        k = classes[cls]
        out: Set[Tuple[str, str]] = set()
        if cls.endswith("Repository") or "/repositories/" in k.path:
            vs = k.views_by_method.get(method, set()) if method else k.all_views
            return {(cls, v) for v in (vs or k.all_views)}
        bodies = [k.methods.get(method, "")] if method and method in k.methods else list(k.methods.values())
        done_own: Set[str] = set()
        while bodies:
            body = bodies.pop()
            for own in re.findall(r"this\.(\w+)\s*\(", body):
                if own in k.methods and own not in done_own and own != method:
                    done_own.add(own)
                    bodies.append(k.methods[own])
            for field, meth in re.findall(r"this\.(\w+)\.(\w+)\s*\(", body):
                target = k.fields.get(field)
                if target:
                    out |= reach(target, meth, seen)
        return out

    for p in files(repo, str(cfg.get("routers") or "src/routers"), "*.ts"):
        text = read(p)
        r = rel(repo, p)
        pm = re.search(r"prefix:\s*[\"']([^\"']+)[\"']", text)
        prefix = pm.group(1) if pm else ""
        for m in re.finditer(r"\.(get|post|put|patch|delete)\(\s*\n?\s*[\"'`](/[^\"'`]*)?[\"'`]", text):
            method, path = m.group(1).upper(), (m.group(2) or "")
            full = (prefix.rstrip("/") + path) or "/"
            rid = g.node(f"route:{method} {full}", file=f"{r}:{lineno(text, m.start())}", layer="api")
            seg = text[m.end(): m.end() + 1500]
            nxt = re.search(r"\.(get|post|put|patch|delete)\(", seg)
            seg = seg[: nxt.start()] if nxt else seg
            for ctl, cmeth in re.findall(r"([A-Z]\w+Controller)\.getInstance\(\)\.(\w+)\(", seg):
                for repo_cls, view in reach(ctl, cmeth, set()):
                    vid = g.node(f"table:{view}", layer="view")
                    g.edge(vid, rid, "read by", f"{classes[repo_cls].path} ({repo_cls})")
                    g.node(f"file:{classes[repo_cls].path}", layer="code")
                    g.edge(vid, f"file:{classes[repo_cls].path}", "read by", classes[repo_cls].path)
                    g.edge(f"file:{classes[repo_cls].path}", rid, "serves", classes[repo_cls].path)
                if ctl in classes:
                    g.node(f"file:{classes[ctl].path}", layer="code")
                    g.edge(f"file:{classes[ctl].path}", rid, "serves", classes[ctl].path)
            g.node(f"file:{r}", layer="code")
            g.edge(f"file:{r}", rid, "serves", r)


# React Router frontend ---------------------------------------------------------------------------

API_PATH = re.compile(r"[`\"'](/(?:[a-z][a-z0-9-]*)(?:/[^`\"'?\s]*)?)(?:\?[^`\"']*)?[`\"']")


def norm_path(p: str) -> str:
    p = re.sub(r"\$\{[^}]+\}", ":param", p.split("?")[0])
    return p.rstrip("/") or "/"


def route_match(front: str, back: str) -> bool:
    a, b = norm_path(front).split("/"), back.split("/")
    if len(a) != len(b):
        return False
    return all(x == y or x == ":param" or y.startswith(":") for x, y in zip(a, b))


@scanner("react_router", 2)
def scan_react_router(g: Graph, cfg: Dict[str, Any], ctx: Dict[str, Any]) -> None:
    """A React Router frontend: `{ path: "x", element: <Comp /> }` routes in the app file → the page
    folder `Comp` is imported from (directly, or through a small wrapper component defined in one of
    `route_files`) → path literals passed to the API helper in that folder's api modules and in api
    modules it imports → the backend route with the same shape. Settings: `repo`, `app` (default
    `src/App.tsx`), `route_files` (extra files with wrapper components), `alias` (import alias of
    `src/`, default `@/`), `api_calls` (helper names before a path literal, default [apiRequest,
    fetch, BASE])."""
    repo = repo_path(cfg, ctx)
    app_rel = str(cfg.get("app") or "src/App.tsx")
    app = read(repo / app_rel)
    if not app:
        ctx["notes"].append(f"react_router {cfg.get('repo')}: {app_rel} not found")
        return
    alias = str(cfg.get("alias") or "@/")
    a = re.escape(alias)
    wrappers = "\n".join(read(repo / f) for f in as_list(cfg.get("route_files")))
    imp_re = rf"import\s+(\w+)\s+from\s+[\"']({a}pages/[^\"']+)[\"']"
    imports = {**dict(re.findall(imp_re, wrappers)), **dict(re.findall(imp_re, app))}
    calls = as_list(cfg.get("api_calls")) or ["apiRequest", "fetch", "BASE"]

    def page_dirs(component: str) -> List[str]:
        if component in imports:
            return [imports[component]]
        m = re.search(rf"(?:function|const)\s+{component}\b(.*?)(?=\n(?:export\s+)?(?:function|const)\s+\w+\b|\Z)", wrappers, re.S)
        if not m:
            return []
        return sorted({imports[c] for c in re.findall(r"<(\w+)", m.group(1)) if c in imports})

    backend_routes = [n for n in g.nodes if n.startswith("route:")]
    api_files = [p for p in files(repo, "src", "*.ts") if "/api/" in str(p) or p.name in ("api.ts", "client.ts")]
    api_paths: Dict[str, List[Tuple[str, str]]] = {}
    for p in api_files:
        text = read(p)
        hits = []
        for m in API_PATH.finditer(text):
            before = text[max(0, m.start() - 120): m.start()]
            if any(c in before for c in calls):
                hits.append((norm_path(m.group(1)), f"{rel(repo, p)}:{lineno(text, m.start())}"))
        api_paths[str(p)] = hits

    for m in re.finditer(r"path:\s*[\"']([^\"']*)[\"'][^}]*?element:\s*<(\w+)", app, re.S):
        path, comp = "/" + m.group(1).lstrip("/"), m.group(2)
        dirs = page_dirs(comp)
        if not dirs:
            continue
        pid = g.node(f"page:{path}", component=comp, layer="page", file=f"{repo.name}/{app_rel}:{lineno(app, m.start())}")
        for d in dirs:
            sub = d[len(alias):] if d.startswith(alias) else d
            pdir = repo / "src" / sub
            pfiles = [pdir.with_suffix(".tsx"), pdir.with_suffix(".ts")] if not pdir.is_dir() else list(pdir.rglob("*.ts*"))
            texts = {str(f): read(f) for f in pfiles if f.is_file()}
            # api modules the page imports from elsewhere
            for t in list(texts.values()):
                for imp in re.findall(rf"from\s+[\"']({a}(?:pages|components)/[^\"']*api[^\"']*)[\"']", t):
                    q = repo / "src" / imp[len(alias):]
                    for f in ([q] if q.suffix else [q.with_suffix(".ts"), q / "index.ts"]):
                        if f.is_file():
                            texts[str(f)] = read(f)
            for f in texts:
                for fp, ev in api_paths.get(f, []):
                    for rid in backend_routes:
                        if route_match(fp, rid.split(" ", 1)[1]):
                            g.edge(rid, pid, "shown on", ev)
            g.node(f"file:{repo.name}/src/{sub}", layer="code")
            g.edge(f"file:{repo.name}/src/{sub}", pid, "renders")


def load_plugin_scanners(folder: Path = Path(__file__).resolve().parent / "scanners") -> List[str]:
    """Scanners added as modules in infra/lineage/scanners/ (TYPE, STAGE, scan)."""
    added = []
    for f in sorted(folder.glob("*.py")) if folder.is_dir() else []:
        spec = importlib.util.spec_from_file_location(f"lineage_scanner_{f.stem}", f)
        if not spec or not spec.loader:
            continue
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if getattr(mod, "TYPE", None) and callable(getattr(mod, "scan", None)):
            SCANNERS[mod.TYPE] = (int(getattr(mod, "STAGE", 1)), mod.scan)
            added.append(mod.TYPE)
    return added


# ------------------------------------------------------------------------------------ by hand

def load_hand(path: Path) -> Dict[str, Any]:
    """knowledge/lineage.yaml (and lineage-sources.yaml) without pyyaml: a tiny YAML subset (lists of
    flat maps whose values are scalars or [a, b] lists), enough for these files."""
    data: Dict[str, Any] = {}
    if not path.is_file():
        return data
    section, cur = None, None
    for raw in path.read_text().splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([a-z_]+):\s*$", line)
        if m:
            section = m.group(1); data[section] = []; continue
        m = re.match(r"^\s*-\s+(\w+):\s*(.*)$", line)
        if m and section:
            cur = {}; data[section].append(cur)
            cur[m.group(1)] = _scalar(m.group(2)); continue
        m = re.match(r"^\s+(\w+):\s*(.*)$", line)
        if m and cur is not None:
            cur[m.group(1)] = _scalar(m.group(2))
    return data


def _scalar(v: str) -> Any:
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        return [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
    return v.strip("'\"")


def apply_hand(g: Graph, hand: Dict[str, Any]) -> List[str]:
    """Adds metrics, page names and owners. Returns names that don't resolve (for `check`)."""
    missing = []
    for p in hand.get("pages", []):
        pid = f"page:{p['path']}"
        if pid not in g.nodes:
            missing.append(f"page {p['path']} (knowledge/lineage.yaml) is not a route any frontend source found")
            g.node(pid, layer="page")
        g.nodes[pid].update({k: v for k, v in (("name", p.get("name")), ("owner", p.get("owner"))) if v})
    for mt in hand.get("metrics", []):
        mid = g.node(f"metric:{mt['id']}", name=mt.get("name"), owner=mt.get("owner"), layer="metric")
        for src in mt.get("from") or []:
            hits = g.resolve(src)
            if not hits:
                missing.append(f"metric {mt['id']}: source {src} not found")
            for h in hits:
                g.edge(h, mid, "computes")
        for page in mt.get("shown_on") or []:
            pid = f"page:{page}"
            if pid not in g.nodes:
                missing.append(f"metric {mt['id']}: page {page} not found")
            g.edge(mid, g.node(pid, layer="page"), "shown on")
    for o in hand.get("owners", []):
        pat = o.get("match", "")
        for nid, n in g.nodes.items():
            if re_glob(pat, nid.split(":", 1)[-1]) and "owner" not in n:
                n["owner"] = o.get("owner")
    return missing


def re_glob(pattern: str, s: str) -> bool:
    return bool(pattern) and re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), s) is not None


# ------------------------------------------------------------------------------------ build

def env_values(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    p = Path(path).expanduser()
    if p.is_file():
        for line in p.read_text().splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip("'\"")
    return {**out, **{k: v for k, v in os.environ.items() if k.startswith("CH_RO_") and v}}


SOURCES_FILES = (ROOT / "knowledge" / "lineage-sources.yaml", Path(__file__).resolve().parent / "sources.example.yaml")


def load_sources(path: Optional[Path] = None) -> Tuple[List[Dict[str, Any]], str]:
    """The sources to scan: AGENT_LINEAGE_SOURCES, else knowledge/lineage-sources.yaml, else the
    example in infra/lineage/sources.example.yaml."""
    cands = [path] if path else ([Path(os.environ["AGENT_LINEAGE_SOURCES"])] if os.environ.get("AGENT_LINEAGE_SOURCES") else list(SOURCES_FILES))
    for f in cands:
        if f and f.is_file():
            return load_hand(f).get("sources", []), str(f)
    return [], ""


def build(repos: Path = REPOS, hand_file: Path = ROOT / "knowledge" / "lineage.yaml",
          env: Optional[Dict[str, str]] = None, views: Optional[List[Dict[str, str]]] = None,
          sources: Optional[List[Dict[str, Any]]] = None) -> Tuple[Graph, List[str]]:
    """Runs every source's scanner (data first, then API routes, then pages), then the hand file."""
    g = Graph()
    notes: List[str] = []
    load_plugin_scanners()
    if sources is None:
        sources, _ = load_sources()
    if env is None:
        env = env_values(os.environ.get("AGENT_OPS_ENV", "~/.config/agent-ops/env"))
    ctx = {"repos": Path(repos), "env": env, "views": views, "notes": notes}
    known = [s for s in sources if s.get("type") in SCANNERS]
    for s in sources:
        if s.get("type") not in SCANNERS:
            notes.append(f"source type {s.get('type')!r} has no scanner (known: {', '.join(sorted(SCANNERS))})")
    for s in sorted(known, key=lambda s: SCANNERS[s["type"]][0]):
        SCANNERS[s["type"]][1](g, s, ctx)
    notes += apply_hand(g, load_hand(hand_file))
    return g, notes


def load(max_age: int = MAX_AGE, rebuild: bool = True) -> Graph:
    """The cached graph; rebuilt when older than a day (or missing)."""
    if CACHE.is_file() and time.time() - CACHE.stat().st_mtime < max_age:
        return Graph.from_json(json.loads(CACHE.read_text()))
    if not rebuild and CACHE.is_file():
        return Graph.from_json(json.loads(CACHE.read_text()))
    g, notes = build()
    save(g, notes)
    return g


def save(g: Graph, notes: List[str]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    data = g.to_json()
    data["notes"] = notes
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(CACHE)


def upstream_terms(g: Graph, name: str, max_depth: int = 3, limit: int = 12) -> List[str]:
    """Search terms for what `name` is built from, nearest first: table names (without database)
    and code file paths. Used by `journal suspects` to look for changes upstream of a broken thing."""
    starts = g.resolve(name)
    out: List[str] = []
    for r in g.walk(starts, "up", max_depth):
        if r["node"] in starts:
            continue
        kind, _, val = r["node"].partition(":")
        term = val.split(".", 1)[-1] if kind == "table" else val if kind == "file" else None
        if term and term not in out:
            out.append(term)
    return out[:limit]


def files_to_nodes(g: Graph, paths: Iterable[str]) -> List[str]:
    """Nodes defined in these repo files (paths relative to their repo, or `repo/path`)."""
    out = []
    for p in paths:
        p = p.strip().lstrip("./")
        for nid, n in g.nodes.items():
            f = (n.get("file") or "").split(":")[0]
            fid = nid[5:] if nid.startswith("file:") else ""
            if any(x and (x == p or x.endswith("/" + p)) for x in (f, fid)) or \
                    (fid and p.startswith(fid.split("/", 1)[-1].rstrip("/") + "/")):
                out.append(nid)
    return sorted(set(out))


# ------------------------------------------------------------------------------------------ CLI

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="lineage", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    for c in ("impact", "upstream"):
        s = sub.add_parser(c); s.add_argument("node"); s.add_argument("--full", action="store_true")
    s = sub.add_parser("files"); s.add_argument("paths", nargs="+"); s.add_argument("--impact", action="store_true")
    sub.add_parser("check")
    sub.add_parser("sources")
    a = ap.parse_args(argv)
    if a.cmd == "sources":
        load_plugin_scanners()
        srcs, f = load_sources()
        print(json.dumps({"file": f, "sources": srcs, "scanner_types": sorted(SCANNERS)}, indent=2))
        return 0
    if a.cmd == "build":
        g, notes = build()
        save(g, notes)
        kinds = defaultdict(int)
        for nid in g.nodes:
            kinds[nid.split(":", 1)[0]] += 1
        print(json.dumps({"nodes": dict(kinds), "edges": len(g.edges), "notes": notes, "cache": str(CACHE)}, indent=2))
        return 0
    g = load() if a.cmd != "check" else Graph()
    if a.cmd in ("impact", "upstream"):
        starts = g.resolve(a.node)
        if not starts:
            print(json.dumps({"error": f"no node matches {a.node!r}"})); return 1
        rows = g.walk(starts, "down" if a.cmd == "impact" else "up")
        print(json.dumps({"matched": starts, "summary": g.summary(rows[len(starts):]),
                          **({"rows": rows} if a.full else {})}, indent=2, ensure_ascii=False))
    elif a.cmd == "files":
        nodes = files_to_nodes(g, a.paths)
        out: Dict[str, Any] = {"nodes": nodes}
        if a.impact:
            rows = g.walk(nodes, "down")
            out["impact"] = g.summary([r for r in rows if r["node"] not in nodes])
        print(json.dumps(out, indent=2, ensure_ascii=False))
    elif a.cmd == "check":
        g, notes = build()
        save(g, notes)
        unowned = [n for n, v in g.nodes.items() if n.split(":")[0] in ("page", "metric") and not v.get("owner")]
        print(json.dumps({"problems": notes, "unowned": unowned}, indent=2, ensure_ascii=False))
        return 1 if notes else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
