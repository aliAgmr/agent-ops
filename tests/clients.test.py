# Offline tests for clients/agent_ops (Cursor/Codex/Kimi Code setup): rendering into a throwaway home
# folder, keeping the person's own entries, idempotence, tool plugins, uninstall, the MCP launcher,
# the guard adapter and Kimi's hooks + deny rules.
# Secrets go to a JSON file instead of the OS keychain (AGENT_OPS_SECRETS_FILE).
# Run: uv run --project clients python tests/clients.test.py
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp())
ENV = {**os.environ, "AGENT_OPS_USER_HOME": str(T), "AGENT_OPS_SECRETS_FILE": str(T / "secrets.json"), "AGENT_OPS_OFFLINE": "1"}
fail = 0


def ok(cond, label, extra=None):
    global fail
    print(("ok   " if cond else "FAIL ") + label + ("" if cond or extra is None else f"\n     {str(extra)[:1500]}"))
    fail |= not cond


def run(*args, stdin=""):
    return subprocess.run([sys.executable, "-m", "agent_ops", *args], input=stdin, capture_output=True,
                          text=True, env=ENV, cwd=ROOT / "clients")


# ---- a person who already has their own Cursor/Codex/Kimi config ----------------------------------
(T / ".cursor").mkdir()
(T / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}, "figma": {"url": "https://other"}}}))
(T / ".cursor" / "hooks.json").write_text(json.dumps({"version": 1, "hooks": {"stop": [{"command": "./mine.sh"}]}}))
(T / ".codex").mkdir()
(T / ".codex" / "config.toml").write_text('model = "x"\n\n[mcp_servers.myown]\ncommand = "y"\n')
(T / ".codex" / "AGENTS.md").write_text("# my notes\n")
(T / ".agents" / "skills" / "handoff").mkdir(parents=True)          # someone else's skill, same name
(T / ".agents" / "skills" / "handoff" / "SKILL.md").write_text("---\nname: handoff\ndescription: mine\n---\n")
K = T / ".kimi-code"
K.mkdir()
(K / "mcp.json").write_text(json.dumps({"mcpServers": {"kmine": {"command": "k"}}}))
(K / "config.toml").write_text('default_model = "k2"\n\n[[hooks]]\nevent = "Stop"\ncommand = "./my-stop.sh"\n\n'
                               '[[permission.rules]]\ndecision = "allow"\npattern = "Read"\n')
(K / "AGENTS.md").write_text("# kimi notes\n")
(T / "secrets.json").write_text(json.dumps({"core.tracker_api_key": "TRACKER-SECRET", "jenkins-tools.jenkins_auth": "Basic JSECRET"}))

r = run("setup", "--client", "cursor", "--client", "codex", "--client", "kimi", "--role", "backend", "--person", "alex",
        "--tools", "gitlab,postgres,clickhouse", "--tool", "jenkins-tools", "--tools", "metabase,figma",
        "--set", "core.agent_mcp_url=https://agents.example.com/agent-mcp",
        "--set", "core.tracker_base_url=https://plane.example.com", "--set", "core.tracker_workspace_slug=acme",
        "--set", "clickhouse-tools.clickhouse_host=localhost", "--set", "jenkins-tools.jenkins_mcp_url=http://localhost:3030/mcp-server/mcp",
        "--non-interactive")
ok(r.returncode == 0, "setup runs", r.stderr)
cfg = json.loads((T / ".agent-ops" / "config.json").read_text())
ok(cfg["tools"] == ["clickhouse-tools", "figma-tools", "gitlab-tools", "jenkins-tools", "metabase-tools", "postgres-tools"],
   "tool plugins from --tools (short names, commas) and --tool", cfg.get("tools"))
ok("'figma' already exists" in r.stderr and "skill handoff" in r.stderr, "conflicts are reported, not overwritten", r.stderr)

cm = json.loads((T / ".cursor" / "mcp.json").read_text())["mcpServers"]
ok(cm["mine"] == {"command": "x"} and cm["figma"] == {"url": "https://other"}, "Cursor: own servers kept", cm)
ok({"plane", "agent-mcp", "gitlab", "postgres", "clickhouse", "jenkins", "metabase"} <= set(cm), "Cursor: role servers added", list(cm))
ok(cm["plane"]["args"][-2:] == ["mcp", "core/plane"], "Cursor: servers start through the launcher")
ok("SECRET" not in (T / ".cursor" / "mcp.json").read_text(), "Cursor: no secret in mcp.json")
ch = json.loads((T / ".cursor" / "hooks.json").read_text())["hooks"]
ok(ch["stop"] == [{"command": "./mine.sh"}], "Cursor: own hooks kept")
ok(all(any("guard --client cursor" in h["command"] and h.get("failClosed") for h in ch[e])
       for e in ("beforeShellExecution", "beforeMCPExecution")), "Cursor: guard hooks fail closed", ch)
ok(any("update --hook cursor" in h["command"] for h in ch["sessionStart"]), "Cursor: update at session start")
ok(all(any("journal --client cursor --phase " + ph in h["command"] and not h.get("failClosed") for h in ch[e])
       for e, ph in (("beforeShellExecution", "pre"), ("beforeMCPExecution", "pre"),
                     ("afterShellExecution", "post"), ("afterMCPExecution", "post"))),
   "Cursor: journal hooks before and after every call, never blocking", ch)
rule = (T / ".cursor" / "plugins" / "local" / "agent-ops" / "rules" / "agent-ops.mdc").read_text()
ok("alwaysApply: true" in rule and "Alex Morgan" in rule and "[agent:alex-backend]" in rule, "Cursor: always-on rule", rule[:400])

text = (T / ".codex" / "config.toml").read_text()
t = tomllib.loads(text)
ok(t["model"] == "x" and t["mcp_servers"]["myown"] == {"command": "y"}, "Codex: own config kept")
ok({"plane", "agent-mcp", "figma", "gitlab"} <= set(t["mcp_servers"]), "Codex: servers added", list(t["mcp_servers"]))
ok(t["mcp_servers"]["figma"] == {"url": "https://mcp.figma.com/mcp"}, "Codex: Figma as a remote URL server (own login)")
ok("SECRET" not in text, "Codex: no secret in config.toml")
xh = json.loads((T / ".codex" / "hooks.json").read_text())["hooks"]
ok(any("guard --client codex" in h["command"] for g in xh["PreToolUse"] for h in g["hooks"]), "Codex: guard hook", xh)
ok(any("mcp__" in g["matcher"] and "Bash" in g["matcher"] for g in xh["PreToolUse"]), "Codex: guard covers shell and MCP")
ok(any("journal --client codex --phase pre" in h["command"] for g in xh["PreToolUse"] for h in g["hooks"]) and
   any("journal --client codex --phase post" in h["command"] for g in xh["PostToolUse"] for h in g["hooks"]),
   "Codex: journal hooks before and after", xh)
agents = (T / ".codex" / "AGENTS.md").read_text()
ok(agents.startswith("# my notes") and "# Team rules (agent-ops)" in agents, "Codex: AGENTS.md block after own notes")

# Kimi Code
km = json.loads((K / "mcp.json").read_text())["mcpServers"]
ok(km["kmine"] == {"command": "k"}, "Kimi: own servers kept", km)
ok({"plane", "agent-mcp", "gitlab", "postgres", "clickhouse", "jenkins", "metabase"} <= set(km), "Kimi: servers added", list(km))
ok(km["plane"]["args"][-2:] == ["mcp", "core/plane"] and km["plane"]["startupTimeoutMs"] == 120000, "Kimi: servers start through the launcher")
ok(km["figma"] == {"url": "https://mcp.figma.com/mcp"}, "Kimi: Figma as a remote URL server (own login)")
ok("SECRET" not in (K / "mcp.json").read_text(), "Kimi: no secret in mcp.json")
ktext = (K / "config.toml").read_text()
kt = tomllib.loads(ktext)
ok(kt["default_model"] == "k2" and any(h.get("command") == "./my-stop.sh" for h in kt["hooks"]), "Kimi: own config and hooks kept", kt)
ours = [h for h in kt["hooks"] if "agent_ops" in h["command"]]
ok(all(set(h) <= {"event", "matcher", "command", "timeout"} for h in kt["hooks"]), "Kimi: [[hooks]] use only the allowed keys", ours)
ok(any(h["event"] == "PreToolUse" and "guard --client kimi" in h["command"] and "Bash" in h["matcher"] and "mcp__" in h["matcher"] for h in ours),
   "Kimi: guard hook on shell and MCP", ours)
ok(any(h["event"] == "PreToolUse" and "journal --client kimi --phase pre" in h["command"] for h in ours)
   and any(h["event"] == "PostToolUse" and "journal --client kimi --phase post" in h["command"] for h in ours),
   "Kimi: journal hooks before and after", ours)
ok(any(h["event"] == "SessionStart" and "update --hook kimi" in h["command"] for h in ours), "Kimi: update at session start")
deny = [r["pattern"] for r in kt["permission"]["rules"] if r["decision"] == "deny"]
ok({"mcp__*__merge_merge_request", "mcp__*__merge_pull_request", "mcp__*__postgres_mcp_modify", "Bash(git push --force*)",
    "mcp__*__triggerBuild"} <= set(deny), "Kimi: static deny rules as a second layer", deny)
ok(kt["permission"]["rules"][0] == {"decision": "allow", "pattern": "Read"}, "Kimi: own permission rules kept, first")
ok("SECRET" not in ktext, "Kimi: no secret in config.toml")
kag = (K / "AGENTS.md").read_text()
ok(kag.startswith("# kimi notes") and "# Team rules (agent-ops)" in kag, "Kimi: AGENTS.md block after own notes")

sk = T / ".agents" / "skills"
ok((sk / "task-protocol" / "SKILL.md").exists() and (sk / "api-contract" / "SKILL.md").exists(), "skills installed")
ok((sk / "handoff" / "SKILL.md").read_text().endswith("description: mine\n---\n"), "someone else's skill left alone")
rv = (sk / "reviewer" / "SKILL.md").read_text() if (sk / "reviewer" / "SKILL.md").exists() else ""
ok(rv.startswith("---\nname: reviewer") and "tools:" not in rv, "Claude subagent `reviewer` installed as a skill", rv[:200])
ok(not (sk / "fe-integration").exists(), "only this role's skills")

# idempotent
before = {p: p.read_text() for p in [T / ".cursor" / "mcp.json", T / ".cursor" / "hooks.json", T / ".codex" / "config.toml",
                                       T / ".codex" / "hooks.json", T / ".codex" / "AGENTS.md",
                                       K / "mcp.json", K / "config.toml", K / "AGENTS.md"]}
r = run("setup", "--non-interactive")
ok(r.returncode == 0 and all(p.read_text() == v for p, v in before.items()), "second setup changes nothing", r.stderr)

# role and tools change: old skills and servers go, new ones come
r = run("setup", "--role", "frontend", "--tools", "gitlab,figma", "--non-interactive")
cm = json.loads((T / ".cursor" / "mcp.json").read_text())["mcpServers"]
ok("postgres" not in cm and "gitlab" in cm and "mine" in cm, "tools change: servers follow the tools", list(cm))
ok((sk / "fe-integration").exists() and not (sk / "api-contract").exists(), "role change: skills follow the role")
t = tomllib.loads((T / ".codex" / "config.toml").read_text())
ok("postgres" not in t["mcp_servers"] and "myown" in t["mcp_servers"], "tools change: Codex servers follow")
km = json.loads((K / "mcp.json").read_text())["mcpServers"]
ok("postgres" not in km and "kmine" in km, "tools change: Kimi servers follow", list(km))
r = run("setup", "--tools", "nosuch", "--non-interactive")
ok(r.returncode != 0 and "unknown tool plugin" in (r.stderr + r.stdout), "unknown tool plugin refused", r.stderr)
run("setup", "--role", "backend", "--tools", "gitlab,postgres,clickhouse,jenkins,metabase,figma", "--non-interactive")

# ---- MCP launcher (dry run: the command it would start, env names only) --------------------------
r = run("mcp", "core/plane", "--dry-run")
ok(r.returncode == 0 and "plane-mcp-ce.py" in r.stdout and "PLANE_API_KEY" in r.stdout and "SECRET" not in r.stdout,
   "launcher: plane gets its key via env, not args", r.stdout + r.stderr)
r = run("mcp", "core/agent-mcp", "--dry-run")
ok("mcp-remote https://agents.example.com/agent-mcp/mcp --header Authorization:${AGENT_OPS_HEADER_0}" in r.stdout
   and "SECRET" not in r.stdout, "launcher: agent-mcp bridged, Bearer header via env", r.stdout)
r = run("mcp", "jenkins-tools/jenkins", "--dry-run")
ok("Authorization:${JENKINS_AUTH}" in r.stdout and "JSECRET" not in r.stdout, "launcher: jenkins header left for mcp-remote", r.stdout)
r = run("mcp", "postgres-tools/postgres")
ok(r.returncode == 1 and "postgres-tools.postgres_ro_url" in r.stderr, "launcher: refuses to start with a missing secret", r.stderr)
r = run("secrets")
ok("core.tracker_api_key: set (secret)" in r.stdout and "TRACKER-SECRET" not in r.stdout and "postgres_ro_url: NOT SET" in r.stdout,
   "secrets list shows set / not set only", r.stdout)

# ---- hook environment: what journal.sh / session-notes.sh get (same names as under Claude Code) ------
sys.path.insert(0, str(ROOT / "clients"))
os.environ.update({k: v for k, v in ENV.items() if k.startswith("AGENT_OPS_")})
from agent_ops import config as C, guard as G  # noqa: E402
he = C.hook_env(C.load_config())
ok(he.get("CLAUDE_PLUGIN_OPTION_TRACKER_API_KEY") == "TRACKER-SECRET" and he.get("CLAUDE_PLUGIN_OPTION_TRACKER_BASE_URL") == "https://plane.example.com"
   and he.get("CLAUDE_PLUGIN_OPTION_TRACKER_WORKSPACE_SLUG") == "acme" and he.get("CLAUDE_PLUGIN_OPTION_ROLE") == "backend"
   and he.get("CLAUDE_PLUGIN_OPTION_AGENT_MCP_URL") == "https://agents.example.com/agent-mcp", "hook env: tracker, agent-mcp and role options", sorted(he))
ok(G.journal_url("https://agents.example.com/agent-mcp") == "https://agents.example.com/agent-mcp/journal"
   and G.journal_url("https://agents.example.com/agent-mcp/mcp/") == "https://agents.example.com/agent-mcp/journal", "journal URL next to agent-mcp")

# ---- guard adapter ----------------------------------------------------------------------------------
MAIN = "git push origin " + "main"  # (split so this file's own text doesn't trip a guard)


def cursor(ev):
    r = run("guard", "--client", "cursor", stdin=json.dumps(ev))
    return json.loads(r.stdout or "{}")


def codex(ev, client="codex"):
    r = run("guard", "--client", client, stdin=json.dumps(ev))
    return r.returncode, r.stderr


def kimi(ev):
    return codex({"hook_event_name": "PreToolUse", "session_id": "s1", "cwd": "/x", **ev}, "kimi")


ok(cursor({"command": MAIN, "cwd": "/x"}).get("permission") == "deny", "cursor: push to main denied")
ok(cursor({"command": "ls -la", "cwd": "/x"}).get("permission") == "allow", "cursor: ls allowed")
d = cursor({"mcp_server_name": "clickhouse", "tool_name": "run_query", "tool_input": json.dumps({"query": "drop table x"}), "command": "python"})
ok(d.get("permission") == "deny" and "write/DDL" in d.get("agent_message", ""), "cursor: ClickHouse DDL denied (MCP, stdio)", d)
ok(cursor({"mcp_server_name": "clickhouse", "tool_name": "run_query", "tool_input": json.dumps({"query": "select 1"})}).get("permission") == "allow",
   "cursor: SELECT allowed")
ok(cursor({"mcp_server_name": "plane", "tool_name": "workitem", "tool_input": json.dumps({"action": "delete"})}).get("permission") == "deny",
   "cursor: Plane delete denied")
rc, err = codex({"tool_name": "Bash", "tool_input": {"command": "git push " + "-f origin feat"}})
ok(rc == 2 and "force-push" in err, "codex: force-push blocked (exit 2 + reason)", err)
ok(codex({"tool_name": "exec_command", "tool_input": {"cmd": ["git", "push", "origin", "ma" + "ster"]}})[0] == 2, "codex: exec_command array form checked")
ok(codex({"tool_name": "exec_command", "tool_input": {"cmd": ["git", "status"]}}) == (0, ""), "codex: harmless command allowed")
ok(codex({"tool_name": "mcp__gitlab__merge_merge_request", "tool_input": {"merge_request_iid": 1}})[0] == 2, "codex: GitLab merge blocked")
ok(codex({"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch"}}) == (0, ""), "codex: file edits not guarded")
rc, err = kimi({"tool_name": "Bash", "tool_input": {"command": MAIN}})
ok(rc == 2 and "BLOCKED" in err, "kimi: push to the main branch blocked (exit 2 + reason)", err)
ok(kimi({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}) == (0, ""), "kimi: harmless command allowed")
ok(kimi({"tool_name": "mcp__github__merge_pull_request", "tool_input": {"pullNumber": 3}})[0] == 2, "kimi: GitHub merge blocked")
ok(kimi({"tool_name": "mcp__postgres__postgres_mcp_modify", "tool_input": {}})[0] == 2, "kimi: DB write blocked")
# devops exemption comes from the configured role
run("setup", "--role", "devops", "--non-interactive")
ok(codex({"tool_name": "mcp__jenkins__triggerBuild", "tool_input": {"jobFullName": "billing-backend-stage"}})[0] == 0, "codex: devops may run a stage job")
ok(codex({"tool_name": "mcp__jenkins__triggerBuild", "tool_input": {"jobFullName": "billing-backend-prod"}})[0] == 2, "codex: devops can't run prod")
kd = [r["pattern"] for r in tomllib.loads((K / "config.toml").read_text())["permission"]["rules"] if r["decision"] == "deny"]
ok("mcp__*__triggerBuild" not in kd and "mcp__*__merge_merge_request" in kd, "kimi: devops keeps CI runs (guard checks the target), merges still denied", kd)
run("setup", "--role", "backend", "--non-interactive")
ok(codex({"tool_name": "mcp__jenkins__triggerBuild", "tool_input": {"jobFullName": "billing-backend-stage"}})[0] == 2, "codex: backend can't run jobs")
chk = run("check", "--quick").stdout
ok(chk.count("guard: blocks push to main") == 3, "check: guard self-test for all three clients", chk)
ok("kimi config.toml" in chk and "fail open" in chk, "check: Kimi hooks, deny rules and the fail-open note", chk)

# ---- session-start update hook: quiet, throttled, never fails ------------------------------------
r = run("update", "--hook", "cursor")
ok(r.returncode == 0 and isinstance(json.loads(r.stdout), dict), "update hook (cursor) answers JSON", r.stdout + r.stderr)
r = run("update", "--hook", "codex")
ok(r.returncode == 0 and r.stdout == "", "update hook throttled: second call within the hour is silent", r.stdout)
r = run("update", "--hook", "kimi")
ok(r.returncode == 0, "update hook (kimi) never fails", r.stderr)

# ---- uninstall ---------------------------------------------------------------------------------------
run("uninstall", "--client", "cursor")
cm = json.loads((T / ".cursor" / "mcp.json").read_text())["mcpServers"]
ch = json.loads((T / ".cursor" / "hooks.json").read_text())["hooks"]
ok(set(cm) == {"mine", "figma"} and set(ch) == {"stop"}, "uninstall cursor: only ours removed", (cm, ch))
ok((sk / "task-protocol").exists(), "uninstall one client: skills stay for the other")
run("uninstall", "--client", "kimi")
km = json.loads((K / "mcp.json").read_text())["mcpServers"]
kt = tomllib.loads((K / "config.toml").read_text())
ok(set(km) == {"kmine"} and kt["hooks"] == [{"event": "Stop", "command": "./my-stop.sh"}]
   and kt["permission"]["rules"] == [{"decision": "allow", "pattern": "Read"}] and (K / "AGENTS.md").read_text() == "# kimi notes\n",
   "uninstall kimi: back to own config", (km, kt))
run("uninstall")
t = tomllib.loads((T / ".codex" / "config.toml").read_text())
ok(set(t["mcp_servers"]) == {"myown"} and (T / ".codex" / "AGENTS.md").read_text() == "# my notes\n", "uninstall codex: back to own config")
ok(not (sk / "task-protocol").exists() and (sk / "handoff").exists(), "uninstall all: our skills removed, theirs kept")
sys.exit(fail)
