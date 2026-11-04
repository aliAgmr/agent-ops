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
    return None  # apply_patch, update_plan, …: not covered by guard.sh


def verdict(event: dict, role: str):
    """→ (allowed, reason). Runs guard.sh with the person's role."""
    bash = C.find_bash()
    guard = C.repo_root() / "plugins" / "core" / "scripts" / "guard.sh"
    if not bash or not guard.exists():
        return False, "agent-ops guard is not installed correctly (bash or guard.sh missing); run `agent-ops check`"
    try:
        r = subprocess.run([bash, str(guard), "--role", role or ""], input=json.dumps(event),
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"agent-ops guard failed to run ({e.__class__.__name__}); blocked to be safe"
    if r.returncode == 2:
        return False, r.stderr.strip() or "BLOCKED by agent-ops guard"
    if r.returncode != 0:
        return False, f"agent-ops guard error (exit {r.returncode}); blocked to be safe"
    return True, ""


def run(client: str) -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        data = {}
    role = C.load_config().get("role", "")
    event = normalize(client, data)
    ok, reason = (True, "") if event is None else verdict(event, role)
    if client == "cursor":
        out = {"continue": True, "permission": "allow"} if ok else {
            "continue": True, "permission": "deny", "user_message": reason, "agent_message": reason}
        print(json.dumps(out))
        return 0
    if ok:
        return 0
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
                               capture_output=True, text=True, timeout=15)
    except Exception:  # noqa: BLE001 - the journal must never get in the way of a tool call
        pass
    if client == "cursor" and phase == "pre":
        print(json.dumps({"continue": True, "permission": "allow"}))
    return 0
