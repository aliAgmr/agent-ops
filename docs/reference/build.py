"""Build the agent-ops reference: one self-contained HTML page plus its data files.

    uv run --with markdown --with pyyaml python docs/reference/build.py [--out DIR] [--offline]

Reads, in order:
  - docs/reference/sections/*.md      the reference text (kept in the repo, edited with every change)
  - git history (origin/main, else HEAD)  the timeline: one entry per merge (MR/PR) or direct commit
  - <log root>/<agent>/*.jsonl        service-agent runs (cost, outcome, turns): AGENT_LOG_ROOT, default
                                      ~/Library/Logs/agent-ops on macOS, /var/log/agents on Linux
  - ~/.claude/projects/*agent-ops-service-<agent>/*.jsonl  run transcripts (tool calls, blocks)
  - <agent-mcp data>/agent-mcp.sqlite3       teammates' questions and messages (counts, no text)
  - <agent-mcp data>/answers-quality.jsonl   claim counts per agent-mcp answer (no text)
  - service/*/.claude/settings.json, service/*/.mcp.json, plugins/*/.mcp.json, skills  tool inventory
  - the task board (optional, through infra/adapters with TRACKER_TRIAGE_TOKEN): items created and
    closed per person over the last 30 days, for the projects in projects.yaml
and writes DIR/index.html + DIR/data/{sections,timeline,reports}.json (default docs/reference/out,
git-ignored). Every report is empty, not an error, when its source doesn't exist, so `--offline`
builds from a fresh clone. Publish DIR wherever your team reads docs (docs/reference/README.md).

Everything that leaves this script goes through redact(): no email addresses, no IP addresses, no
internal hostnames or URLs, no token-like strings, nothing on tools/leak-terms.txt. People appear by
first name.
"""
from __future__ import annotations

import datetime as dt
import glob
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import markdown
import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
LOGS = Path(os.environ.get("AGENT_LOG_ROOT") or ("~/Library/Logs/agent-ops" if sys.platform == "darwin" else "/var/log/agents")).expanduser()
TRANSCRIPTS = Path("~/.claude/projects").expanduser()
MCP_DB = Path(os.environ.get("AGENT_MCP_DATA_DIR", "~/.local/share/agent-mcp")).expanduser() / "agent-mcp.sqlite3"
AGENTS = ["triage", "analyst", "release", "nightly"]
DAYS = 30
NOW = dt.datetime.now(dt.timezone.utc)
SINCE = NOW - dt.timedelta(days=DAYS)


# ---- redaction ---------------------------------------------------------------------------------
def load_people() -> dict:
    for f in ("people.yaml",):      # the real team, git-ignored; the template's examples are not people
        p = ROOT / f
        if p.exists():
            return (yaml.safe_load(p.read_text()) or {}).get("people") or {}
    return {}


PEOPLE = load_people()
EMAIL_TO_NAME = {}
for key, p in PEOPLE.items():
    first = (p.get("full_name") or key).split()[0]
    for k in ("email", "figma_email"):
        if p.get(k):
            EMAIL_TO_NAME[str(p[k]).strip().lower()] = first


def env_values() -> dict[str, str]:
    env = dict(os.environ)
    f = Path(os.environ.get("AGENT_OPS_ENV", "~/.config/agent-ops/env")).expanduser()
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"'))
    return env


def internal_hosts() -> dict[str, str]:
    """Hostnames this deployment uses → placeholder: from the env file and routing.yaml's code_hosts.
    Public hosts (github.com) are left alone."""
    out = {}
    env = env_values()
    names = {"TRACKER_BASE_URL": "<tracker-host>", "METABASE_URL": "<metabase-host>", "JENKINS_URL": "<jenkins-host>",
             "CH_RO_HOST": "<clickhouse-host>", "AGENT_MCP_PUBLIC_URL": "<agent-mcp-host>"}
    vals = [(env.get(k, ""), ph) for k, ph in names.items()]
    routing = ROOT / "routing.yaml"
    if routing.exists():
        for key, h in ((yaml.safe_load(routing.read_text()) or {}).get("code_hosts") or {}).items():
            vals.append((str((h or {}).get("url") or ""), f"<code-host-{key}>"))
    for v, ph in vals:
        host = urllib.parse.urlparse(v).hostname if "://" in v else v.split(":")[0].split("/")[0]
        if host and host not in ("github.com", "gitlab.com", "localhost", "127.0.0.1") and not host.endswith("example.com"):
            out[host] = ph
    return out


def leak_terms() -> list[re.Pattern]:
    f = ROOT / "tools" / "leak-terms.txt"
    if not f.exists():
        return []
    return [re.compile(l.strip(), re.I) for l in f.read_text().splitlines() if l.strip() and not l.startswith("#")]


HOSTS = internal_hosts()
TERMS = leak_terms()
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# loopback and bind-all addresses are not internal hosts; every other IPv4 address is redacted
IPV4 = re.compile(r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
TOKENISH = re.compile(r"\b(?:glpat-|plane_api_|sk-ant-|xox[bp]-|ghp_|gho_|github_pat_)[\w-]{8,}|\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")


def redact(s: str) -> str:
    if not s:
        return s
    s = EMAIL.sub(lambda m: EMAIL_TO_NAME.get(m.group(0).lower(), "<email>"), s)
    for host, ph in sorted(HOSTS.items(), key=lambda x: -len(x[0])):
        s = s.replace(host, ph)
    for rx in TERMS:
        s = rx.sub("<redacted>", s)
    s = IPV4.sub("<internal-address>", s)
    return TOKENISH.sub("<secret>", s)


def name_of(email: str) -> str:
    return EMAIL_TO_NAME.get((email or "").strip().lower(), "someone outside people.yaml")


# ---- sections ----------------------------------------------------------------------------------
def md_to_html(text: str) -> str:
    # mermaid fences render natively in artifacts as <pre class="mermaid">
    text = re.sub(r"```mermaid\n(.*?)```", lambda m: '<pre class="mermaid">' + html.escape(m.group(1)) + "</pre>",
                  text, flags=re.S)
    return markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "toc"],
                             extension_configs={"toc": {"permalink": False}})


def sections() -> list[dict]:
    out = []
    for f in sorted((HERE / "sections").glob("*.md")):
        text = redact(f.read_text())
        m = re.match(r"#\s+(.+)\n", text)
        title = m.group(1).strip() if m else f.stem
        body = text[m.end():] if m else text
        sid = re.sub(r"^\d+-", "", f.stem)
        out.append({"id": sid, "title": title, "file": f"docs/reference/sections/{f.name}",
                    "words": len(body.split()), "html": md_to_html(body)})
    return out


# ---- timeline ----------------------------------------------------------------------------------
def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def has_rev(rev: str) -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "-q", rev], capture_output=True).returncode == 0


def timeline() -> list[dict]:
    ref = "origin/main" if has_rev("origin/main") else "HEAD"
    out = []
    if not has_rev(ref):
        return out                      # a fresh repository with no commits yet
    for line in git("log", "--first-parent", ref, "--format=%H%x1f%P%x1f%aI%x1f%s").splitlines():
        h, parents, date, subject = line.split("\x1f")
        parents = parents.split()
        merge = len(parents) > 1
        body = git("log", "-1", "--format=%b", h)
        mr = re.search(r"!(\d+)", body) or re.search(r"Merge pull request #(\d+)", subject)
        if merge:
            commits = []
            for c in git("log", f"{parents[0]}..{parents[1]}", "--format=%h%x1f%s%x1f%b%x1e").split("\x1e"):
                c = c.strip("\n")
                if c:
                    ch, cs, cb = (c.split("\x1f") + ["", ""])[:3]
                    commits.append({"hash": ch, "subject": redact(cs),
                                    "body": redact(re.sub(r"\n?Co-Authored-By:.*", "", cb).strip())})
            title = commits[0]["subject"] if commits else subject
            base = parents[0]
        else:
            commits = [{"hash": h[:7], "subject": redact(subject),
                        "body": redact(re.sub(r"\n?Co-Authored-By:.*", "", body).strip())}]
            title, base = subject, f"{h}^"
        stat = git("diff", "--shortstat", base, h).strip() if has_rev(base) else ""
        files = [l for l in git("diff", "--name-status", base, h).splitlines()] if stat else []
        branch = re.search(r"Merge branch '([^']+)'", subject) or re.search(r"Merge pull request #\d+ from \S+?/(\S+)", subject)
        kind = (re.match(r"(\w+)[(:!]", title) or [None, "change"])[1]
        out.append({"hash": h[:7], "date": date, "title": redact(title), "kind": kind,
                    "branch": branch.group(1) if branch else None, "mr": int(mr.group(1)) if mr and merge else None,
                    "stat": stat, "files": [redact(f) for f in files[:80]], "more_files": max(0, len(files) - 80),
                    "commits": commits})
    return out


# ---- reports -----------------------------------------------------------------------------------
def parse_ts(s) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def agent_runs() -> dict:
    per_agent = {}
    for a in AGENTS:
        days = defaultdict(lambda: {"runs": 0, "ok": 0, "failed": 0, "cost": 0.0})
        outcomes, recent, total_cost, turns = Counter(), [], 0.0, []
        skipped = Counter()
        for f in sorted(glob.glob(str(LOGS / a / "*.jsonl"))):
            for line in open(f, errors="replace"):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                t = parse_ts(r.get("started"))
                if not isinstance(r, dict) or not t or t < SINCE:
                    continue
                res = r.get("result") if isinstance(r.get("result"), dict) else {}
                cost = float(res.get("total_cost_usd") or 0)
                d = days[t.date().isoformat()]
                d["runs"] += 1
                d["cost"] += cost
                d["ok" if r.get("outcome") == "ok" else "failed"] += 1
                outcomes[r.get("outcome") or "unknown"] += 1
                total_cost += cost
                if res.get("num_turns"):
                    turns.append(res["num_turns"])
                summary = redact((res.get("result") or "").strip().split("\n")[0][:240])
                recent.append({"started": r.get("started"), "outcome": r.get("outcome"), "cost": round(cost, 2),
                               "turns": res.get("num_turns"), "model": r.get("model"),
                               "reason": redact((r.get("reason") or "")[:160]), "summary": summary})
        sk = LOGS / a / "skipped.log"
        if sk.exists():
            for line in sk.read_text(errors="replace").splitlines():
                t = parse_ts(line.split(" ", 1)[0])
                if t and t >= SINCE:
                    why = line.split(" ", 1)[1] if " " in line else ""
                    skipped["budget" if why.startswith("budget") else "paused" if why.startswith("paused")
                            else "already running" if "still active" in why else "other"] += 1
        per_agent[a] = {"days": dict(sorted(days.items())), "outcomes": dict(outcomes),
                        "cost": round(total_cost, 2), "runs": sum(outcomes.values()),
                        "median_turns": sorted(turns)[len(turns) // 2] if turns else None,
                        "skipped": dict(skipped), "recent": recent[-15:][::-1]}
    return per_agent


def quality() -> dict:
    """How well the agents back their write-ups: the analyst's runs (claims, unsourced claims, the
    fresh checker's verdict; run logs `quality`) and agent-mcp's answers (answers-quality.jsonl)."""
    runs = []
    for f in sorted(glob.glob(str(LOGS / "analyst" / "*.jsonl"))):
        for line in open(f, errors="replace"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            t = parse_ts(r.get("started")) if isinstance(r, dict) else None
            q = r.get("quality") if isinstance(r, dict) else None
            if t and t >= SINCE and isinstance(q, dict):
                runs.append({"started": r.get("started"), "item": q.get("item"), "claims": q.get("claims"),
                             "unsourced": q.get("unsourced"), "rate": q.get("rate"), "check": q.get("check")})
    claims = sum(r["claims"] or 0 for r in runs)
    unsourced = sum(r["unsourced"] or 0 for r in runs)
    checked = [r["check"] for r in runs if isinstance(r["check"], dict)]
    answers = []
    qf = MCP_DB.parent / "answers-quality.jsonl"
    if qf.exists():
        for line in qf.read_text(errors="replace").splitlines():
            try:
                a = json.loads(line)
            except ValueError:
                continue
            if isinstance(a, dict) and a.get("ts") and dt.datetime.fromtimestamp(a["ts"], dt.timezone.utc) >= SINCE:
                answers.append(a)
    a_claims = sum(a.get("claims") or 0 for a in answers)
    a_uns = sum(a.get("unsourced") or 0 for a in answers)
    return {
        "analyst": {"runs": len(runs), "claims": claims, "unsourced": unsourced,
                    "rate": round(unsourced / claims, 3) if claims else None,
                    "checked_runs": len(checked), "not_checked": sum(1 for r in runs if not isinstance(r["check"], dict)),
                    "supported": sum(c["supported"] for c in checked), "check_total": sum(c["total"] for c in checked),
                    "fixed": sum(c.get("fixed", 0) for c in checked), "recent": runs[-15:][::-1]},
        "answers": {"count": len(answers), "claims": a_claims, "unsourced": a_uns,
                    "rate": round(a_uns / a_claims, 3) if a_claims else None,
                    "without_sources": sum(1 for a in answers if not a.get("sources"))},
    }


def tool_usage() -> dict:
    out = {}
    for a in AGENTS + ["agent-mcp"]:
        calls, errors, blocked = Counter(), Counter(), Counter()
        sessions = 0
        for d in glob.glob(str(TRANSCRIPTS / f"*agent-host-agent-ops-service-{a}")):
            for f in glob.glob(f"{d}/*.jsonl"):
                if dt.datetime.fromtimestamp(os.path.getmtime(f), dt.timezone.utc) < SINCE:
                    continue
                sessions += 1
                names = {}
                for line in open(f, errors="replace"):
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    for c in (e.get("message") or {}).get("content") or []:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "tool_use":
                            n = re.sub(r"^mcp__(plugin_[\w-]+_)?", "", c.get("name", "?"))
                            names[c.get("id")] = n
                            calls[n] += 1
                        elif c.get("type") == "tool_result" and c.get("is_error"):
                            n = names.get(c.get("tool_use_id"), "?")
                            text = json.dumps(c.get("content"))
                            if "BLOCKED by agent-ops guard" in text:
                                blocked[n] += 1
                            elif "permission" in text.lower() or "denied" in text.lower():
                                blocked[n] += 1
                            else:
                                errors[n] += 1
        out[a] = {"sessions": sessions, "calls": dict(calls.most_common(25)), "total_calls": sum(calls.values()),
                  "errors": dict(errors.most_common(10)), "blocked": dict(blocked.most_common(10))}
    return out


def teammates(tracker: bool) -> dict:
    people = {k: {"name": (p.get("full_name") or k).split()[0], "role": p.get("role") or "",
                  "questions": 0, "conversations": 0, "messages_sent": 0, "messages_received": 0,
                  "tasks_created": None, "tasks_closed": None} for k, p in PEOPLE.items()}
    by_email = {str(p.get("email") or "").lower(): k for k, p in PEOPLE.items() if p.get("email")}
    if MCP_DB.exists():
        db = sqlite3.connect(f"file:{MCP_DB}?mode=ro", uri=True)
        since = SINCE.timestamp()
        for email, n in db.execute("select lower(owner_email), count(*) from conversations where created_at >= ? group by 1", (since,)):
            if email in by_email:
                people[by_email[email]]["conversations"] = n
        for email, n in db.execute("""select lower(c.owner_email), count(*) from tickets t join conversations c
                                      on c.id = t.conversation_id where t.created_at >= ? group by 1""", (since,)):
            if email in by_email:
                people[by_email[email]]["questions"] = n
        for col, field in (("sender", "messages_sent"), ("recipient", "messages_received")):
            for who, n in db.execute(f"select {col}, count(*) from messages where created_at >= ? and status = 'sent' group by 1", (since,)):
                k = who if who in people else by_email.get(str(who).lower())
                if k in people:
                    people[k][field] = n
    if tracker:
        tracker_activity(people, by_email)
    return people


def tracker_activity(people: dict, by_email: dict):
    """Items created and closed per person on the task board, through infra/adapters (any tracker
    that implements TrackerAdapter). Skipped quietly when the adapter or its token isn't there."""
    env = env_values()
    if not env.get("TRACKER_TRIAGE_TOKEN"):
        return
    try:
        sys.path.insert(0, str(ROOT / "infra"))
        import adapters  # noqa: PLC0415  (infra/adapters, the tracker/code-host interfaces)
        tr = adapters.tracker(env=env, token=env["TRACKER_TRIAGE_TOKEN"])
        projects = (yaml.safe_load((ROOT / "projects.yaml").read_text()) or {}).get("projects") or {}
        wanted = {str(p.get("tracker_project_id")) for p in projects.values() if p}
        members = {}
        for p in people.values():
            p["tasks_created"], p["tasks_closed"] = 0, 0
        for pr in tr.projects():
            if wanted and str(pr["id"]) not in wanted:
                continue
            for m in tr.members(pr["id"]):
                members[m["id"]] = str(m.get("email") or "").lower()
            for it in tr.list_items(pr["id"]):
                k = by_email.get(members.get(it.get("created_by"), ""))
                if k and (parse_ts(it.get("created_at")) or NOW) >= SINCE:
                    people[k]["tasks_created"] += 1
                closed = parse_ts(it.get("completed_at"))
                if it.get("state_group") == "completed" and closed and closed >= SINCE:
                    for a in it.get("assignees") or []:
                        k = by_email.get(members.get(a, ""))
                        if k:
                            people[k]["tasks_closed"] += 1
    except Exception as e:  # noqa: BLE001 - the report still builds without the task board
        print(f"task-board activity skipped: {type(e).__name__}: {e}", file=sys.stderr)


def inventory() -> dict:
    agents = {}
    for d in sorted((ROOT / "service").iterdir()):
        s, m = d / ".claude" / "settings.json", d / ".mcp.json"
        if not d.is_dir():
            continue
        perms = json.loads(s.read_text()).get("permissions", {}) if s.exists() else {}
        mcps = json.loads(m.read_text()).get("mcpServers", {}) if m.exists() else {}
        skills = [p.parent.name for p in (d / ".claude" / "skills").glob("*/SKILL.md")]
        agents[d.name] = {"allow": perms.get("allow", []), "deny": perms.get("deny", []),
                          "mcp": {k: mcp_desc(v) for k, v in mcps.items()}, "skills": skills}
    plugins = {}
    for d in sorted((ROOT / "plugins").iterdir()):
        if not d.is_dir():
            continue
        m = d / ".mcp.json"
        meta = d / ".claude-plugin" / "plugin.json"
        skills = []
        for f in sorted(d.glob("skills/*/SKILL.md")):
            fm = re.search(r"^description:\s*(.+)$", f.read_text(), re.M)
            skills.append({"name": f.parent.name, "description": redact((fm.group(1) if fm else "").strip())[:400]})
        plugins[d.name] = {"version": json.loads(meta.read_text()).get("version") if meta.exists() else None,
                           "mcp": {k: mcp_desc(v) for k, v in (json.loads(m.read_text()).get("mcpServers", {}) if m.exists() else {}).items()},
                           "skills": skills}
    guard = ROOT / "plugins/core/scripts/guard.sh"
    rules = re.findall(r"^# (\d+)\. (.+)$", guard.read_text(), re.M) if guard.exists() else []
    return {"service_agents": agents, "plugins": plugins,
            "guard_rules": [{"n": int(n), "rule": redact(t)} for n, t in rules]}


def mcp_desc(v: dict) -> dict:
    args = " ".join(v.get("args") or [])
    pkg = re.search(r"(@?[\w./-]+(?:==|@)[\w.-]+|@[\w-]+/[\w.-]+|mcp-[\w-]+|figma-developer-mcp|plane-mcp-server)", args)
    env = v.get("env") or {}
    ro = any(k.endswith("READ_ONLY_MODE") and str(x).lower() == "true" for k, x in env.items()) or \
        any(re.search(r"_RO_|READONLY|RO_URL", str(x)) for x in env.values())
    return {"type": v.get("type") or "stdio", "package": pkg.group(1) if pkg else (v.get("command") or v.get("url") and "remote http"),
            "credentials": sorted({m for x in env.values() for m in re.findall(r"\$\{(\w+)\}", str(x))}),
            "read_only_credential": ro}


# ---- page --------------------------------------------------------------------------------------
def main(argv):
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else HERE / "out"
    (out / "data").mkdir(parents=True, exist_ok=True)
    ref = "origin/main" if has_rev("origin/main") else "HEAD"
    head = git("rev-parse", "--short", ref).strip() if has_rev(ref) else "uncommitted"
    meta = {"generated_at": NOW.isoformat(timespec="seconds"), "commit": head, "window_days": DAYS}
    secs = sections()
    tl = timeline()
    reports = {"meta": meta, "runs": agent_runs(), "quality": quality(), "tools": tool_usage(),
               "teammates": teammates(not ({"--offline", "--no-plane", "--no-tracker"} & set(argv))), "inventory": inventory()}
    data = {"sections.json": {"meta": meta, "sections": secs}, "timeline.json": {"meta": meta, "entries": tl},
            "reports.json": reports}
    for name, obj in data.items():
        text = json.dumps(obj, ensure_ascii=False, indent=1)
        leak = EMAIL.search(text) or IPV4.search(text)
        if leak:
            raise SystemExit(f"refusing to write {name}: unredacted {leak.group(0)[:3]}… found")
        (out / "data" / name).write_text(text)
    page = (HERE / "page.html").read_text()
    nav = "".join(f'<li><a href="#{x["id"]}"><span class="num">{i:02d}</span>{html.escape(x["title"])}</a></li>'
                  for i, x in enumerate(secs, 1))
    docs = "".join(f'<section class="doc" id="{x["id"]}"><p class="eyebrow"><span>Reference {i:02d}</span>'
                   f'<span>{html.escape(x["file"])}</span><span>{x["words"]:,} words</span></p>'
                   f'<h1>{html.escape(x["title"])}</h1>{x["html"]}</section>' for i, x in enumerate(secs, 1))
    blob = json.dumps({"reports": reports, "timeline": {"entries": tl}}, ensure_ascii=False).replace("</", "<\\/")
    page = page.replace("<!--NAV-->", nav).replace("<!--SECTIONS-->", docs).replace("/*DATA*/", blob)
    if EMAIL.search(page) or IPV4.search(page):
        raise SystemExit("refusing to write index.html: unredacted email or IP address found")
    (out / "index.html").write_text(page)
    print(json.dumps({"out": str(out), "sections": len(secs), "timeline": len(tl), "commit": head,
                      "runs": {a: r["runs"] for a, r in reports["runs"].items()}}))


if __name__ == "__main__":
    main(sys.argv[1:])
