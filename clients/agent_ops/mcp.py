"""MCP servers: what the client config gets, and the launcher that starts a server with its secrets.

Cursor, Codex and Kimi Code start every agent-ops server as `<python> -m agent_ops mcp <plugin>/<server>`. The
launcher reads the plugin's .mcp.json, fills in `${user_config.*}` (secrets from the keychain) and
`${CLAUDE_PLUGIN_ROOT}`, and starts the real server with stdio passed through. So no secret is ever
written into ~/.cursor/mcp.json, ~/.codex/config.toml or ~/.kimi-code/mcp.json, and every server sees the same values it
gets under Claude Code.

Remote servers: without headers (Figma: OAuth) they are written as plain URL servers and each client
does its own browser login. With headers (agent-mcp: Bearer <task-board token>) the launcher bridges
them through `mcp-remote` over stdio, with the header value in an environment variable that
mcp-remote expands itself, so the secret isn't on any command line.
"""
import os
import re
import shutil
import subprocess
import sys

from . import config as C

VAR = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")


def native_url(spec: dict):
    """URL of a remote server the client should connect to directly (no headers to fill in)."""
    if spec.get("type") in ("http", "sse") and not spec.get("headers"):
        return spec["url"]
    return None


def resolve(plugin: C.Plugin, name: str, cfg: dict) -> dict:
    """→ {command, args, env, missing}: what to start for this server. `missing` lists options
    without a value (the server can't work until they're set)."""
    spec = plugin.servers[name]
    missing = []

    def fill(s: str, keep=()):
        def rep(m):
            k = m.group(1)
            if k == "CLAUDE_PLUGIN_ROOT":
                return str(plugin.root)
            if k == "CLAUDE_PLUGIN_DATA":
                return str(C.state_dir() / "data" / plugin.name)
            if k.startswith("user_config."):
                key = k.split(".", 1)[1]
                v = C.option_value(cfg, plugin, key)
                if v is None or v == "":
                    missing.append(f"{plugin.name}.{key}")
                    return ""
                return str(v)
            if k in keep:
                return m.group(0)  # left for the child (mcp-remote expands ${VAR} in its args)
            return os.environ.get(k, m.group(0))
        return VAR.sub(rep, s)

    env = {k: fill(str(v)) for k, v in (spec.get("env") or {}).items()}
    if spec.get("type") in ("http", "sse"):
        args = ["-y", "mcp-remote", fill(spec["url"])]
        if spec["url"].startswith("http://"):
            args.append("--allow-http")
        for i, (h, v) in enumerate((spec.get("headers") or {}).items()):
            var = f"AGENT_OPS_HEADER_{i}"
            env[var] = fill(str(v))
            args += ["--header", f"{h}:${{{var}}}"]
        command = "npx"
    else:
        command = fill(spec["command"])
        keep = tuple(env) if any("mcp-remote" in a for a in spec.get("args", [])) else ()
        args = [fill(str(a), keep) for a in spec.get("args", [])]
    env["CLAUDE_PLUGIN_ROOT"] = str(plugin.root)
    env["CLAUDE_PLUGIN_DATA"] = str(C.state_dir() / "data" / plugin.name)
    return {"command": command, "args": args, "env": env, "missing": sorted(set(missing))}


def program(command: str):
    if command == "bash":
        return C.find_bash()
    return shutil.which(command) or command


def find(cfg: dict, ref: str):
    plugin_name, _, server = ref.partition("/")
    for p in C.plugins_of(cfg):
        if p.name == plugin_name and server in p.servers:
            return p, server
    raise SystemExit(f"agent-ops: no MCP server {ref} for role {cfg.get('role')}")


def launch(ref: str, dry_run=False) -> int:
    cfg = C.load_config()
    if not cfg.get("role"):
        print("agent-ops: not set up yet; run `agent-ops setup`", file=sys.stderr)
        return 1
    plugin, server = find(cfg, ref)
    r = resolve(plugin, server, cfg)
    if r["missing"]:
        print(f"agent-ops: {ref} can't start, not set: {', '.join(r['missing'])}. "
              "Run `agent-ops setup` (or `agent-ops secrets set <name>`).", file=sys.stderr)
        return 1
    exe = program(r["command"])
    if not exe:
        print(f"agent-ops: {r['command']} not found (on Windows: install Git for Windows for bash)", file=sys.stderr)
        return 1
    if dry_run:  # for tests and `agent-ops check`: values of env vars are never printed
        print(" ".join([exe] + r["args"]))
        print("env: " + ", ".join(sorted(r["env"])))
        return 0
    env = {**os.environ, **r["env"]}
    os.makedirs(env["CLAUDE_PLUGIN_DATA"], exist_ok=True)
    if C.WINDOWS:  # no real exec on Windows: run it with our stdio and pass its exit code on
        return subprocess.call([exe] + r["args"], env=env)
    os.execvpe(exe, [exe] + r["args"], env)
