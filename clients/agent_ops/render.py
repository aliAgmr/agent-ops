"""Writes each client's files from the plugins. Only our own entries are touched: other MCP servers,
hooks, skills and text the person already has stay as they are. Every file is backed up once
(<file>.agent-ops.bak) before the first change.

| | Cursor | Codex | Kimi Code |
|---|---|---|---|
| skills | ~/.agents/skills/<name> (all three read it) | same | same |
| always-on rules | ~/.cursor/plugins/local/agent-ops (rule, alwaysApply) | block in ~/.codex/AGENTS.md | block in ~/.kimi-code/AGENTS.md |
| MCP servers | ~/.cursor/mcp.json | block in ~/.codex/config.toml | ~/.kimi-code/mcp.json |
| guard, journal + update hooks | ~/.cursor/hooks.json | ~/.codex/hooks.json | block of [[hooks]] in ~/.kimi-code/config.toml |
| static deny rules (2nd layer) | — | — | [[permission.rules]] in the same block |

Kimi Code ($KIMI_CODE_HOME, default ~/.kimi-code): hooks fail open (a guard that crashes or times
out lets the call through), so the block also carries static `deny` permission rules for the
hardest rules, and `agent-ops check` says so.
"""
import json
import re
import shutil
from pathlib import Path

from . import config as C
from . import mcp as M

MARK = ".agent-ops-managed"          # file inside every skill folder we installed
BEGIN = "# >>> agent-ops (managed by `agent-ops setup`; edits inside are overwritten) >>>"
END = "# <<< agent-ops <<<"
MD_BEGIN = "<!-- >>> agent-ops (managed by `agent-ops setup`; edits inside are overwritten) >>> -->"
MD_END = "<!-- <<< agent-ops <<< -->"


def backup(path: Path):
    b = path.with_name(path.name + ".agent-ops.bak")
    if path.exists() and not b.exists():
        shutil.copy2(path, b)


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    backup(path)
    path.write_text(text, encoding="utf-8")
    return True


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except ValueError:
        raise SystemExit(f"agent-ops: {path} isn't valid JSON; fix or move it, then run setup again")


def hook_cmd(client: str, *args: str) -> str:
    py = C.python_exe()
    rest = " ".join(["-m", "agent_ops", *args])
    if C.WINDOWS and client == "cursor":   # Cursor runs hook commands through PowerShell
        return f'& "{py}" {rest}'
    return f'"{py}" {rest}'


# ---- what to install ------------------------------------------------------------------------------
def servers(cfg: dict, plugins: list) -> dict:
    """name → {"url": …} or {"command", "args"} (the launcher). Later plugins win on a name clash
    (a role plugin's gitlab over another's), like Claude Code's last-loaded server."""
    out = {}
    for p in plugins:
        for name, spec in p.servers.items():
            url = M.native_url(spec)
            out[name] = {"url": url} if url else {
                "command": C.python_exe(), "args": ["-m", "agent_ops", "mcp", f"{p.name}/{name}"]}
    return out


def team_name(cfg: dict) -> str:
    return cfg.get("team_name") or "your team"


def rules_text(cfg: dict, plugins: list) -> str:
    who = cfg.get("full_name") or cfg.get("person") or "the person using this computer"
    tag = f"[agent:{cfg.get('person', '<person>')}-{cfg['role']}]"
    skills = ", ".join([f"`{s.name}`" for p in plugins for s in p.skills] + [f"`{a.stem}`" for p in plugins for a in p.agents])
    return f"""\
# Team rules (agent-ops)

You work for {who}, role **{cfg['role']}**, on {team_name(cfg)}. These rules come from the team's
agent-ops plugins and apply in every project.

- Tasks live on the team's **task board** (MCP server `plane` by default). Whenever you read,
  create, update or comment on a task, follow the `task-protocol` skill. Tag your comments `{tag}`.
- Before starting any task or touching a repo, database, CI job, design file, BI tool or code host,
  read the `scope-guard` skill: what your role may and may not touch. Work for another team goes
  through the `handoff` skill. Before creating a task, check for duplicates (including `Backlog`
  items) as `task-protocol` says.
- Hard rules, enforced by the agent-ops guard hook: no writes to any database, no CI job runs,
  no merges or deployments, no push to `main`/`master`, no force-push, never delete tasks.
  If the guard blocks something, tell the user and stop; don't look for a way around it.
- Every write you make is recorded in the team's action journal (author, value before, undo);
  `blame`, `suspects`, `lineage` and `revert_plan` (MCP server `agent-mcp`) read it.
- Questions about parts of the project you can't see (other repos, data, Jenkins, history): the
  `ask-project` skill (MCP server `agent-mcp`).
- Messaging a teammate on Telegram when the user asks: the `message-teammate` skill (MCP server
  `agent-mcp`); always show the user the recipients and exact text and send only after they agree.
- Skill names in these documents may carry a plugin prefix (`core:task-protocol`,
  `role-backend:api-contract`): they are the skills named after the colon.
- Your skills: {skills}.
"""


# ---- skills (shared by both clients) --------------------------------------------------------------
def sync_skills(plugins: list, warn) -> list:
    root = C.home() / ".agents" / "skills"
    root.mkdir(parents=True, exist_ok=True)
    wanted = {s.name: s for p in plugins for s in p.skills}
    wanted.update({a.stem: a for p in plugins for a in p.agents})
    for name, src in wanted.items():
        dst = root / name
        if dst.exists() and not (dst / MARK).exists():
            warn(f"skill {name}: {dst} exists and isn't ours; left it alone (ours not installed)")
            continue
        if dst.exists():
            shutil.rmtree(dst)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:  # a Claude subagent file → a skill: same name/description front matter, `tools` dropped
            dst.mkdir(parents=True)
            text = "\n".join(l for l in src.read_text(encoding="utf-8").splitlines() if not l.startswith("tools:")) + "\n"
            (dst / "SKILL.md").write_text(text, encoding="utf-8")
        (dst / MARK).write_text("Installed by agent-ops setup from the team's plugins; replaced on update.\n", encoding="utf-8")
    for d in root.iterdir():   # skills a previous role/version had but this one doesn't
        if d.is_dir() and (d / MARK).exists() and d.name not in wanted:
            shutil.rmtree(d)
    return sorted(wanted)


# ---- Cursor ---------------------------------------------------------------------------------------
def cursor(cfg: dict, plugins: list, warn, owned: dict) -> dict:
    base = C.home() / ".cursor"
    # MCP servers
    path = base / "mcp.json"
    data = read_json(path, {})
    data.setdefault("mcpServers", {})
    mine = set(owned.get("cursor_servers", []))
    new_owned = []
    for name in list(data["mcpServers"]):
        if name in mine:
            del data["mcpServers"][name]
    for name, entry in servers(cfg, plugins).items():
        if name in data["mcpServers"]:
            warn(f"Cursor MCP server {name!r} already exists in {path} and isn't ours; left it (ours skipped)")
            continue
        data["mcpServers"][name] = entry if "url" in entry else {"type": "stdio", **entry}
        new_owned.append(name)
    write(path, json.dumps(data, indent=2) + "\n")
    # Hooks: guard before shell commands and MCP calls (blocks if the hook itself fails), update at start.
    path = base / "hooks.json"
    data = read_json(path, {"version": 1, "hooks": {}})
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    for ev in list(hooks):
        hooks[ev] = [h for h in hooks[ev] if "agent_ops" not in h.get("command", "")]
        if not hooks[ev]:
            del hooks[ev]
    for ev in ("beforeShellExecution", "beforeMCPExecution"):
        hooks.setdefault(ev, []).append({"command": hook_cmd("cursor", "guard", "--client", "cursor"),
                                         "timeout": 30, "failClosed": True})
    # Action journal: every write is reported (never blocks: no failClosed).
    for ev, phase in (("beforeShellExecution", "pre"), ("beforeMCPExecution", "pre"),
                      ("afterShellExecution", "post"), ("afterMCPExecution", "post")):
        hooks.setdefault(ev, []).append({"command": hook_cmd("cursor", "journal", "--client", "cursor", "--phase", phase),
                                         "timeout": 20})
    hooks.setdefault("sessionStart", []).append({"command": hook_cmd("cursor", "update", "--hook", "cursor"),
                                                 "timeout": 60})
    write(path, json.dumps(data, indent=2) + "\n")
    # Always-on rules: a local Cursor plugin with one alwaysApply rule (user rules aren't files).
    plug = base / "plugins" / "local" / "agent-ops"
    write(plug / ".cursor-plugin" / "plugin.json", json.dumps({
        "name": "agent-ops", "version": cfg.get("plugins_version", "0"),
        "description": "Team rules (rendered by agent-ops setup)."}, indent=2) + "\n")
    write(plug / "rules" / "agent-ops.mdc",
          "---\ndescription: Team rules (agent-ops)\nalwaysApply: true\n---\n\n" + rules_text(cfg, plugins))
    return {"cursor_servers": new_owned}


def uninstall_cursor(owned: dict):
    base = C.home() / ".cursor"
    path = base / "mcp.json"
    if path.exists():
        data = read_json(path, {})
        for n in owned.get("cursor_servers", []):
            data.get("mcpServers", {}).pop(n, None)
        write(path, json.dumps(data, indent=2) + "\n")
    path = base / "hooks.json"
    if path.exists():
        data = read_json(path, {})
        hooks = data.get("hooks", {})
        for ev in list(hooks):
            hooks[ev] = [h for h in hooks[ev] if "agent_ops" not in h.get("command", "")]
            if not hooks[ev]:
                del hooks[ev]
        write(path, json.dumps(data, indent=2) + "\n")
    shutil.rmtree(base / "plugins" / "local" / "agent-ops", ignore_errors=True)


# ---- Codex ----------------------------------------------------------------------------------------
def toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a valid TOML basic string here


def replace_block(text: str, begin: str, end: str, block: str) -> str:
    pat = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.S)
    text = pat.sub("", text).rstrip("\n")
    if not block:
        return text + "\n" if text else ""
    return (text + "\n\n" if text else "") + begin + "\n" + block.rstrip("\n") + "\n" + end + "\n"


def codex(cfg: dict, plugins: list, warn, owned: dict) -> dict:
    base = C.home() / ".codex"
    path = base / "config.toml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    outside = replace_block(text, BEGIN, END, "")
    lines, new_owned = [], []
    for name, entry in servers(cfg, plugins).items():
        if re.search(r"^\s*\[\s*mcp_servers\.\"?" + re.escape(name) + r"\"?\s*[\].]", outside, re.M):
            warn(f"Codex MCP server {name!r} already exists in {path} and isn't ours; left it (ours skipped)")
            continue
        lines.append(f"[mcp_servers.{toml_str(name)}]")
        if "url" in entry:
            lines.append(f"url = {toml_str(entry['url'])}")
        else:
            lines.append(f"command = {toml_str(entry['command'])}")
            lines.append("args = [" + ", ".join(toml_str(a) for a in entry["args"]) + "]")
            lines.append("startup_timeout_sec = 120")  # uvx/npx download on first start
            lines.append("tool_timeout_sec = 300")
        lines.append("")
        new_owned.append(name)
    write(path, replace_block(text, BEGIN, END, "\n".join(lines)))
    # Hooks
    path = base / "hooks.json"
    data = read_json(path, {"hooks": {}})
    hooks = data.setdefault("hooks", {})
    for ev in list(hooks):
        groups = []
        for g in hooks[ev]:
            g["hooks"] = [h for h in g.get("hooks", []) if "agent_ops" not in h.get("command", "")]
            if g["hooks"]:
                groups.append(g)
        hooks[ev] = groups
        if not groups:
            del hooks[ev]
    hooks.setdefault("PreToolUse", []).append({
        "matcher": "^(Bash|exec_command|shell|local_shell|mcp__.*)$",
        "hooks": [{"type": "command", "command": hook_cmd("codex", "guard", "--client", "codex"),
                   "timeout": 30, "statusMessage": "agent-ops guard"}]})
    for ev, phase in (("PreToolUse", "pre"), ("PostToolUse", "post")):   # action journal
        hooks.setdefault(ev, []).append({
            "matcher": "^(Bash|exec_command|shell|local_shell|mcp__.*)$",
            "hooks": [{"type": "command", "command": hook_cmd("codex", "journal", "--client", "codex", "--phase", phase),
                       "timeout": 20, "statusMessage": "agent-ops journal"}]})
    hooks.setdefault("SessionStart", []).append({
        "matcher": "startup|resume",
        "hooks": [{"type": "command", "command": hook_cmd("codex", "update", "--hook", "codex"),
                   "timeout": 60, "statusMessage": "agent-ops update check"}]})
    write(path, json.dumps(data, indent=2) + "\n")
    # Always-on rules
    path = base / "AGENTS.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    write(path, replace_block(text, MD_BEGIN, MD_END, rules_text(cfg, plugins)))
    return {"codex_servers": new_owned}


def uninstall_codex(owned: dict):
    base = C.home() / ".codex"
    for name, (b, e) in (("config.toml", (BEGIN, END)), ("AGENTS.md", (MD_BEGIN, MD_END))):
        path = base / name
        if path.exists():
            write(path, replace_block(path.read_text(encoding="utf-8"), b, e, ""))
    path = base / "hooks.json"
    if path.exists():
        data = read_json(path, {})
        hooks = data.get("hooks", {})
        for ev in list(hooks):
            hooks[ev] = [g for g in hooks[ev] if all("agent_ops" not in h.get("command", "") for h in g.get("hooks", []))]
            if not hooks[ev]:
                del hooks[ev]
        write(path, json.dumps(data, indent=2) + "\n")


# ---- Kimi Code ------------------------------------------------------------------------------------
KIMI_MATCHER = "^(Bash|mcp__.*)$"
# Static deny rules: Kimi's hooks fail open, so the hardest rules are also permission rules the CLI
# enforces itself. MCP rules match tool names only (no arguments), shell rules match the command.
KIMI_DENY_ALWAYS = [
    "mcp__*__merge_merge_request", "mcp__*__merge_pull_request",
    "mcp__*__approve_deployment", "mcp__*__create_deployment", "mcp__*__update_deployment", "mcp__*__delete_deployment",
    "mcp__*__delete_pipeline", "mcp__*__erase_pipeline_job", "mcp__*__updateBuild",
    "mcp__*__postgres_mcp_modify", "mcp__*__postgres_mcp_bulk_load_csv",
    "Bash(psql*)", "Bash(clickhouse-client*)", "Bash(clickhouse client*)", "Bash(clickhouse-local*)",
    "Bash(git push --force*)", "Bash(git push -f*)",
]
# DevOps may run non-production pipelines and CI jobs (the guard checks the target); everyone else may not.
KIMI_DENY_UNLESS_DEVOPS = [
    "mcp__*__triggerBuild", "mcp__*__rebuildBuild", "mcp__*__replayBuild",
    "mcp__*__create_pipeline", "mcp__*__trigger_pipeline", "mcp__*__play_pipeline_job",
    "mcp__*__play_pipeline_jobs", "mcp__*__retry_pipeline", "mcp__*__retry_pipeline_job",
]


def kimi_deny_rules(role: str) -> list:
    return KIMI_DENY_ALWAYS + ([] if role == "devops" else KIMI_DENY_UNLESS_DEVOPS)


def kimi(cfg: dict, plugins: list, warn, owned: dict) -> dict:
    base = C.kimi_home()
    # MCP servers: ~/.kimi-code/mcp.json, the same {"mcpServers": …} shape as Claude Code.
    path = base / "mcp.json"
    data = read_json(path, {})
    data.setdefault("mcpServers", {})
    mine = set(owned.get("kimi_servers", []))
    new_owned = []
    for name in list(data["mcpServers"]):
        if name in mine:
            del data["mcpServers"][name]
    for name, entry in servers(cfg, plugins).items():
        if name in data["mcpServers"]:
            warn(f"Kimi MCP server {name!r} already exists in {path} and isn't ours; left it (ours skipped)")
            continue
        data["mcpServers"][name] = entry if "url" in entry else {
            **entry, "startupTimeoutMs": 120000, "toolTimeoutMs": 300000}   # uvx/npx download on first start
        new_owned.append(name)
    write(path, json.dumps(data, indent=2) + "\n")
    # Hooks and deny rules: a managed block at the end of config.toml. [[hooks]] may only carry
    # event, matcher, command and timeout (other keys stop the file from loading).
    path = base / "config.toml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = ["# Guard: blocks a tool call that breaks the team's hard rules (exit 2 = block).",
             "[[hooks]]", 'event = "PreToolUse"', f"matcher = {toml_str(KIMI_MATCHER)}",
             f"command = {toml_str(hook_cmd('kimi', 'guard', '--client', 'kimi'))}", "timeout = 30", "",
             "# Action journal: reports writes (before and after); never blocks.",
             "[[hooks]]", 'event = "PreToolUse"', f"matcher = {toml_str(KIMI_MATCHER)}",
             f"command = {toml_str(hook_cmd('kimi', 'journal', '--client', 'kimi', '--phase', 'pre'))}", "timeout = 20", "",
             "[[hooks]]", 'event = "PostToolUse"', f"matcher = {toml_str(KIMI_MATCHER)}",
             f"command = {toml_str(hook_cmd('kimi', 'journal', '--client', 'kimi', '--phase', 'post'))}", "timeout = 20", "",
             "# Keeps the team's skills, rules and servers current (at most once an hour).",
             "[[hooks]]", 'event = "SessionStart"', 'matcher = "startup|resume"',
             f"command = {toml_str(hook_cmd('kimi', 'update', '--hook', 'kimi'))}", "timeout = 60", "",
             "# Second layer: Kimi hooks fail open, so these are refused by the CLI itself."]
    for pat in kimi_deny_rules(cfg["role"]):
        lines += ["[[permission.rules]]", 'decision = "deny"', f"pattern = {toml_str(pat)}",
                  'reason = "agent-ops hard rule (see the scope-guard skill)"', ""]
    write(path, replace_block(text, BEGIN, END, "\n".join(lines)))
    # Always-on rules: Kimi's global instructions file.
    path = base / "AGENTS.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    write(path, replace_block(text, MD_BEGIN, MD_END, rules_text(cfg, plugins)))
    return {"kimi_servers": new_owned}


def uninstall_kimi(owned: dict):
    base = C.kimi_home()
    for name, (b, e) in (("config.toml", (BEGIN, END)), ("AGENTS.md", (MD_BEGIN, MD_END))):
        path = base / name
        if path.exists():
            write(path, replace_block(path.read_text(encoding="utf-8"), b, e, ""))
    path = base / "mcp.json"
    if path.exists():
        data = read_json(path, {})
        for n in owned.get("kimi_servers", []):
            data.get("mcpServers", {}).pop(n, None)
        write(path, json.dumps(data, indent=2) + "\n")


def remove_skills():
    root = C.home() / ".agents" / "skills"
    if root.exists():
        for d in root.iterdir():
            if d.is_dir() and (d / MARK).exists():
                shutil.rmtree(d)
