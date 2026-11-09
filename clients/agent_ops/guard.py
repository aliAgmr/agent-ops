"""Hook adapter: runs the core plugin's guard.sh (the same rules as under Claude Code) for Cursor,
Codex and Kimi Code hooks.

guard.sh reads Claude Code's PreToolUse JSON ({"tool_name": "Bash" | "mcp__<server>__<tool>",
"tool_input": {...}}) and exits 2 with a reason to block. This module turns each client's hook
input into that shape and the guard's verdict into the client's answer:

- Cursor beforeShellExecution {command, cwd} / beforeMCPExecution {tool_name, tool_input (a JSON
  string), mcp_server_name}: answers {"permission": "allow" | "deny", ...} on stdout.
- Codex PreToolUse {tool_name, tool_input}: Bash/exec_command become Bash; MCP tools are already
  mcp__<server>__<tool>. Blocks with exit 2 and the reason on stderr.
- Kimi Code PreToolUse {hook_event_name, tool_name, tool_input, …} (snake_case, Claude-shaped): the
  shell tool is `Bash`, MCP tools are mcp__<server>__<tool>. Blocks with exit 2 and the reason on
  stderr, like Codex. Kimi hooks fail open: if this process crashes or times out, Kimi lets the
  call through; the static deny rules setup writes into ~/.kimi-code/config.toml are the backstop.
- Codex apply_patch becomes an Edit of the files it touches with only the lines it adds (guard
  rules 10 and 11). Kimi's own file tools and Cursor file edits (no hook before them) aren't checked.
- Rule 11 (protected files) answers "ask": Cursor shows a permission prompt; Codex and Kimi can't
  ask, so they block and tell the person to make the change themselves.

If the guard can't run (no bash, broken install), the call is blocked: a missing guard must never
mean "anything goes".
"""
import json
import shlex
import subprocess
import sys

from . import config as C

SHELL_TOOLS = {"Bash", "bash", "shell", "local_shell", "exec_command", "unified_exec"}


def normalize(client: str, data: dict):
    """→ {"tool_name", "tool_input"} for guard.sh, or None when there's nothing to check."""
    if client == "cursor":
        if "mcp_server_name" in data or ("tool_name" in data and "tool_input" in data):
            raw = data.get("tool_input")
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except ValueError:
                args = {"raw": raw}
            server = data.get("mcp_server_name") or "unknown"
            return {"tool_name": f"mcp__{server}__{data.get('tool_name', '')}", "tool_input": args}
        if "command" in data:
            return {"tool_name": "Bash", "tool_input": {"command": data["command"], "cwd": data.get("cwd", "")}}
        return None
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    if tool in SHELL_TOOLS:
        cmd = ti.get("command", ti.get("cmd", "")) if isinstance(ti, dict) else ti
        if isinstance(cmd, list):
            cmd = shlex.join(str(c) for c in cmd)
        return {"tool_name": "Bash", "tool_input": {"command": str(cmd)}}
    if tool.startswith("mcp__"):
        return {"tool_name": tool, "tool_input": ti}
    if tool == "apply_patch":
        return patch_event(ti)
    return None  # update_plan, …: not covered by guard.sh


def patch_event(ti) -> dict | None:
    """Codex apply_patch → an Edit for guard.sh: the files it touches (rule 11, protected files) and
    only the lines it adds (rule 10, secrets; removing a secret stays possible)."""
    values = ti.values() if isinstance(ti, dict) else [ti]
    patch = next((v for v in values if isinstance(v, str) and "*** Begin Patch" in v), None)
    if patch is None:
        return None
    files, added = [], []
    for line in patch.splitlines():
        for head in ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: "):
            if line.startswith(head):
                files.append(line[len(head):].strip())
        if line.startswith("+"):
            added.append(line[1:])
    return {"tool_name": "Edit", "tool_input": {"edits": [{"file_path": f} for f in files],
                                                 "new_string": "\n".join(added)}}


def secret_env(cfg: dict, role: str) -> dict:
    """The person's sensitive plugin options as CLAUDE_PLUGIN_OPTION_<KEY>, as Claude Code passes
    them to hooks, so guard.sh rule 10 knows their exact values. Never printed or logged."""
    env = {}
    try:
        for p in C.plugins_for(role, C.repo_root()):
            for key, spec in p.options.items():
                if spec.get("sensitive"):
                    v = C.option_value(cfg, p, key)
                    if v:
                        env[f"CLAUDE_PLUGIN_OPTION_{key.upper()}"] = str(v)
    except (Exception, SystemExit):  # noqa: BLE001 - shapes still apply without the values
        pass
    return env


def verdict(event: dict, role: str, extra_env: dict | None = None):
    """→ (decision, reason): decision "allow", "deny" or "ask" (guard.sh rule 11: a person decides).
    Runs guard.sh with the person's role."""
    import os
    bash = C.find_bash()
    guard = C.repo_root() / "plugins" / "core" / "scripts" / "guard.sh"
    if not bash or not guard.exists():
        return "deny", "agent-ops guard is not installed correctly (bash or guard.sh missing); run `agent-ops check`"
    try:
        r = subprocess.run([bash, str(guard), "--role", role or ""], input=json.dumps(event),
                           env={**os.environ, **(extra_env or {})},
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "deny", f"agent-ops guard failed to run ({e.__class__.__name__}); blocked to be safe"
    if r.returncode == 2:
        return "deny", r.stderr.strip() or "BLOCKED by agent-ops guard"
    if r.returncode != 0:
        return "deny", f"agent-ops guard error (exit {r.returncode}); blocked to be safe"
    try:
        out = json.loads(r.stdout or "{}").get("hookSpecificOutput", {})
    except ValueError:
        out = {}
    if out.get("permissionDecision") == "ask":
        return "ask", out.get("permissionDecisionReason") or "agent-ops guard: this needs your OK"
    return "allow", ""


def run(client: str) -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    cfg = C.load_config()
    role = cfg.get("role", "")
    event = normalize(client, data)
    decision, reason = ("allow", "") if event is None else verdict(event, role, secret_env(cfg, role))
    if client == "cursor":
        out = {"continue": True, "permission": "allow"} if decision == "allow" else {
            "continue": True, "permission": decision, "user_message": reason, "agent_message": reason}
        print(json.dumps(out))
        return 0
    if decision == "allow":
        return 0
    if decision == "ask":   # Codex/Kimi hooks can't ask the person: the person makes this edit themselves
        reason = f"BLOCKED by agent-ops guard: {reason}. This client can't ask for your OK, so make this change yourself."
    print(reason, file=sys.stderr)
    return 2


def journal_url(agent_mcp_url: str) -> str:
    """https://agents.example.com/agent-mcp/mcp → https://agents.example.com/agent-mcp/journal"""
    u = agent_mcp_url.rstrip("/")
    return (u[: -len("/mcp")] if u.endswith("/mcp") else u) + "/journal"


def run_journal(client: str, phase: str) -> int:
    """Hook adapter for the action journal: reports write tool calls to agent-mcp through the core
    plugin's journal.sh, as the person (their task-board token from the keychain). Never blocks."""
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    event = normalize(client, data) if isinstance(data, dict) else None
    try:
        bash = C.find_bash()
        script = C.repo_root() / "plugins" / "core" / "scripts" / "journal.sh"
        if event and bash and script.exists():
            cfg = C.load_config()
            core = next(p for p in C.plugins_of(cfg) if p.name == "core")
            key = C.option_value(cfg, core, "tracker_api_key")
            if key:
                import os
                payload = {**event, "tool_response": data.get("tool_response", data.get("result_json",
                           data.get("tool_output", data.get("tool_result", data.get("output"))))),
                           "tool_use_id": data.get("tool_use_id") or data.get("call_id") or "",
                           "session_id": data.get("session_id") or data.get("conversation_id") or "",
                           "cwd": data.get("cwd") or "", "transcript_path": data.get("transcript_path") or ""}
                env = {**os.environ, **C.hook_env(cfg), "AGENT_JOURNAL_CLIENT": client,
                       "CLAUDE_PLUGIN_DATA": str(C.state_dir() / "data" / "core")}
                mcp_url = C.option_value(cfg, core, "agent_mcp_url")
                if mcp_url:   # the journal endpoint sits next to agent-mcp's /mcp
                    env.setdefault("AGENT_JOURNAL_URL", journal_url(str(mcp_url)))
                subprocess.run([bash, str(script), phase], input=json.dumps(payload), env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    except Exception:  # noqa: BLE001 - the journal must never get in the way of a tool call
        pass
    if client == "cursor" and phase == "pre":
        print(json.dumps({"continue": True, "permission": "allow"}))
    return 0
