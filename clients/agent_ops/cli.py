"""agent-ops: set up your team's agent tools in Cursor, Codex and/or Kimi Code.

  agent-ops setup --client cursor [--client codex] [--client kimi] --role frontend --person noor
  agent-ops secrets [list | set <plugin>.<option>]
  agent-ops check [--quick]
  agent-ops update            (also runs by itself when a Cursor/Codex/Kimi session starts)
  agent-ops uninstall [--client cursor]

Secrets are typed into this terminal (hidden) and kept in the OS keychain (macOS Keychain, Windows
Credential Manager, Linux Secret Service); they never go into a config file or a chat.
"""
import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config as C
from . import guard as G
from . import mcp as M
from . import render as R

UPDATE_EVERY = 3600  # seconds between update checks at session start


def warn(msg):
    print(f"  ! {msg}", file=sys.stderr)


def git(*args, timeout=30):
    return subprocess.run(["git", "-C", str(C.repo_root()), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def head() -> str:
    r = git("rev-parse", "--short", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def people() -> dict:
    """people.yaml (falling back to the example team in people.template.yaml), read without a YAML
    library (only `key:` and `full_name:`/`role:` lines)."""
    out, cur = {}, None
    lines = []
    for name in ("people.yaml", "people.template.yaml"):
        try:
            lines = (C.repo_root() / name).read_text(encoding="utf-8").splitlines()
            break
        except OSError:
            continue
    for line in lines:
        if line.startswith("  ") and not line.startswith("   ") and line.rstrip().endswith(":"):
            cur = line.strip()[:-1]
            out[cur] = {}
        elif cur and line.startswith("    ") and ":" in line:
            k, v = line.strip().split(":", 1)
            out[cur][k] = v.split("#")[0].strip()
    return out


# ---- setup --------------------------------------------------------------------------------------
def ask_options(cfg, plugins, sets, interactive):
    cfg.setdefault("values", {})
    for p in plugins:
        for key, spec in p.options.items():
            full = f"{p.name}.{key}"
            if p.name == "core" and key == "role":
                continue
            if full in sets:
                if spec.get("sensitive"):
                    raise SystemExit(f"{full} is a secret: enter it when setup asks (hidden), not with --set")
                cfg["values"][full] = sets.pop(full)
                continue
            have = C.option_value(cfg, p, key)
            if have not in (None, "") or not interactive:
                continue
            title = spec.get("title", key)
            desc = spec.get("description", "")
            print(f"\n{title} ({full})\n  {desc}")
            if spec.get("sensitive"):
                v = getpass.getpass("  value (hidden, Enter to skip): ").strip()
                if v:
                    C.set_secret(full, v)
            else:
                v = input("  value (Enter to skip): ").strip()
                if v:
                    cfg["values"][full] = v
    if sets:
        raise SystemExit("unknown option(s) for this role: " + ", ".join(sets))


def render_all(cfg) -> list:
    plugins = C.plugins_of(cfg)
    cfg["plugins_version"] = "+".join(f"{p.name}@{p.version}" for p in plugins)
    warnings = []
    owned = cfg.setdefault("owned", {})
    R.sync_skills(plugins, warnings.append)
    for client in C.CLIENTS:
        if client in cfg.get("clients", []):
            owned.update(getattr(R, client)(cfg, plugins, warnings.append, owned))
    cfg["rendered_at"] = head()
    C.save_config(cfg)
    return warnings


def cmd_setup(a):
    cfg = C.load_config()
    if a.role:
        cfg["role"] = a.role
    if not cfg.get("role"):
        raise SystemExit("--role is required the first time (one of: " + ", ".join(C.ROLES) + ")")
    if a.person:
        cfg["person"] = a.person
        cfg["full_name"] = people().get(a.person, {}).get("full_name", "") or cfg.get("full_name", "")
    if a.tool is not None:
        cfg["tools"] = C.tool_names(a.tool)
    clients = set(cfg.get("clients", [])) | set(a.client or [])
    if not clients:
        raise SystemExit("--client cursor, --client codex and/or --client kimi")
    cfg["clients"] = sorted(clients)
    sets = dict(s.split("=", 1) for s in (a.set or []))
    plugins = C.plugins_of(cfg)
    if not cfg.get("tools"):
        warn("no tool plugins chosen (--tools gitlab,postgres,…): only the task board and agent-mcp servers are set up; "
             f"available: {', '.join(C.available_tools())}")
    print(f"agent-ops setup: role {cfg['role']}, plugins {', '.join(p.name for p in plugins)}, "
          f"clients {', '.join(cfg['clients'])}")
    ask_options(cfg, plugins, sets, interactive=not a.non_interactive and sys.stdin.isatty())
    for w in render_all(cfg):
        warn(w)
    missing = C.missing_options(cfg, plugins)
    print("\nDone. Written:")
    if "cursor" in cfg["clients"]:
        print("  Cursor: ~/.cursor/mcp.json, ~/.cursor/hooks.json, ~/.cursor/plugins/local/agent-ops, ~/.agents/skills")
    if "codex" in cfg["clients"]:
        print("  Codex:  ~/.codex/config.toml, ~/.codex/hooks.json, ~/.codex/AGENTS.md, ~/.agents/skills")
    if "kimi" in cfg["clients"]:
        print(f"  Kimi:   {C.kimi_home()}/mcp.json, config.toml (hooks + deny rules block), AGENTS.md, ~/.agents/skills")
    if missing:
        print("Still not set (those servers won't start): " + ", ".join(f"{p.name}.{k}" for p, k in missing))
        print("  Set one with: agent-ops secrets set <name>   (or run setup again)")
    print("\nNext:")
    if "cursor" in cfg["clients"]:
        print("  - Cursor: restart it. Settings → MCP should list the agent-ops servers; figma asks you to log in.")
    if "codex" in cfg["clients"]:
        print("  - Codex: start it and run /hooks once to review and trust the two agent-ops hooks "
              "(guard + update); until then they don't run. Figma: `codex mcp login figma`.")
    if "kimi" in cfg["clients"]:
        print("  - Kimi Code: start a new session; /mcp should list the agent-ops servers. Its hooks fail open "
              "(a crashed guard lets a call through), so the hardest rules are also deny rules in config.toml.")
    print("  - Then: agent-ops check")


# ---- secrets ------------------------------------------------------------------------------------
def cmd_secrets(a):
    cfg = C.load_config()
    if not cfg.get("role"):
        raise SystemExit("run agent-ops setup first")
    plugins = C.plugins_of(cfg)
    opts = {f"{p.name}.{k}": (p, k, s) for p in plugins for k, s in p.options.items()}
    if a.action == "set":
        if a.name not in opts:
            raise SystemExit("unknown option; one of: " + ", ".join(opts))
        p, k, spec = opts[a.name]
        if not spec.get("sensitive"):
            cfg.setdefault("values", {})[a.name] = input(f"{spec.get('title', k)}: ").strip()
            C.save_config(cfg)
        else:
            v = getpass.getpass(f"{spec.get('title', k)} (hidden): ").strip()
            if not v:
                raise SystemExit("nothing entered; unchanged")
            C.set_secret(a.name, v)
        print(f"{a.name}: set")
        return
    for name, (p, k, spec) in opts.items():
        v = C.option_value(cfg, p, k)
        state = "set" if v not in (None, "") else ("NOT SET" if spec.get("required") else "not set (optional)")
        shown = "" if spec.get("sensitive") or v in (None, "") else f" = {v}"
        print(f"{name}: {state}{shown}{' (secret)' if spec.get('sensitive') else ''}")


# ---- update -------------------------------------------------------------------------------------
def cmd_update(a):
    """Pull the repo and re-render when it changed. As a session-start hook (--hook): at most once an
    hour, never fails the session, and prints a note for the agent only when something happened."""
    cfg = C.load_config()
    if not cfg.get("role"):
        return 0
    stamp = C.state_dir() / "last-update-check"
    if a.hook and not a.force:
        try:
            if time.time() - stamp.stat().st_mtime < UPDATE_EVERY:
                return emit(a.hook, session_notes(cfg))
        except OSError:
            pass
    C.state_dir().mkdir(parents=True, exist_ok=True)
    stamp.touch()
    notes = []
    try:
        before = head()
        # AGENT_OPS_OFFLINE (tests): re-render only, never touch the network.
        r = git("pull", "--ff-only", "-q", timeout=40) if not os.environ.get("AGENT_OPS_OFFLINE") else None
        if r is not None and r.returncode != 0:
            notes.append("agent-ops couldn't update itself (git pull failed: " + (r.stderr.strip().splitlines() or ["?"])[-1]
                         + "). Tell the user to run `agent-ops update` in a terminal.")
        after = head()
        if after != cfg.get("rendered_at") or before != after:
            warnings = render_all(cfg)
            plugins = C.plugins_of(cfg)
            notes.append(f"agent-ops updated the team's skills, rules and MCP servers to {after} "
                         "(they take effect in the next session).")
            missing = C.missing_options(cfg, plugins)
            if missing:
                notes.append("New settings need a value: " + ", ".join(f"{p.name}.{k}" for p, k in missing)
                             + ". Ask the user to run `agent-ops setup` in a terminal (secrets are typed there, never in chat).")
            notes += warnings
    except Exception as e:  # noqa: BLE001 - a session must start even if the update breaks
        notes.append(f"agent-ops update failed ({e.__class__.__name__}: {e}). Tell the user to run `agent-ops update`.")
    if not a.hook:
        print("\n".join(notes) or f"agent-ops is up to date ({head()}).")
        return 0
    return emit(a.hook, "\n".join(n for n in [" ".join(notes), session_notes(cfg)] if n))


def session_notes(cfg) -> str:
    """The core plugin's SessionStart note (recurring lessons, open directives), run with the same
    CLAUDE_PLUGIN_OPTION_* variables Claude Code would give it. Empty on any problem."""
    if os.environ.get("AGENT_OPS_OFFLINE"):
        return ""
    bash = C.find_bash()
    script = C.repo_root() / "plugins" / "core" / "scripts" / "session-notes.sh"
    try:
        env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(C.state_dir() / "data" / "core"), **C.hook_env(cfg)}
        r = subprocess.run([bash, str(script)], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - a session must start anyway
        return ""


def emit(hook, text):
    if hook == "cursor":
        print(json.dumps({"additional_context": text} if text else {}))
    elif text:
        print(text)
    return 0


# ---- check --------------------------------------------------------------------------------------
def handshake(argv, env, timeout=150):
    """Start an MCP server and wait for its answer to `initialize`."""
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "agent-ops-check", "version": "1"}}}
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             env=env, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return False, str(e)
    try:
        p.stdin.write(json.dumps(req) + "\n")
        p.stdin.flush()
        deadline = time.time() + timeout
        import selectors
        sel = selectors.DefaultSelector() if not C.WINDOWS else None
        if sel:
            sel.register(p.stdout, selectors.EVENT_READ)
        while time.time() < deadline:
            if sel and not sel.select(timeout=1):
                if p.poll() is not None:
                    return False, f"exited with {p.returncode}"
                continue
            line = p.stdout.readline()
            if not line:
                return False, f"exited with {p.poll()}"
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == 1:
                return ("result" in msg), (msg.get("result", {}).get("serverInfo", {}).get("name", "") or json.dumps(msg.get("error")))
        return False, "no answer (timeout)"
    finally:
        p.kill()


def cmd_check(a):
    cfg = C.load_config()
    ok = True

    def line(good, what, detail=""):
        nonlocal ok
        ok &= good
        print(f"{'ok  ' if good else 'FAIL'} {what}{(': ' + detail) if detail else ''}")

    if not cfg.get("role"):
        line(False, "setup", "run agent-ops setup first")
        return 1
    plugins = C.plugins_of(cfg)
    line(True, "config", f"role {cfg['role']}, person {cfg.get('person', '?')}, tools {', '.join(cfg.get('tools', [])) or 'none'}, "
                         f"clients {', '.join(cfg.get('clients', []))}, repo {head()}")
    missing = C.missing_options(cfg, plugins)
    line(not missing, "settings", "all set" if not missing else "not set: " + ", ".join(f"{p.name}.{k}" for p, k in missing))
    line(bool(C.find_bash()), "bash (for the guard)", C.find_bash() or "not found: install Git for Windows")
    # Guard: must block a push to main and a DB write, and allow a harmless command.
    for client in cfg.get("clients", []):
        cases = [({"command": "git push origin " + "main", "cwd": "."} if client == "cursor" else
                  {"tool_name": "Bash", "tool_input": {"command": "git push origin " + "main"}}, False),
                 ({"command": "ls", "cwd": "."} if client == "cursor" else
                  {"tool_name": "Bash", "tool_input": {"command": "ls"}}, True),
                 ({"mcp_server_name": "postgres", "tool_name": "postgres_mcp_modify", "tool_input": "{}"} if client == "cursor" else
                  {"tool_name": "mcp__postgres__postgres_mcp_modify", "tool_input": {}}, False)]
        good = all((G.verdict(G.normalize(client, ev), cfg["role"])[0] == "allow") == allow for ev, allow in cases)
        line(good, f"{client} guard", "blocks push to main and DB writes, allows ls" if good else "unexpected verdict")
    # Client files
    if "cursor" in cfg.get("clients", []):
        d = R.read_json(C.home() / ".cursor" / "hooks.json", {})
        line(any("agent_ops" in h.get("command", "") for h in d.get("hooks", {}).get("beforeShellExecution", [])),
             "cursor hooks.json")
        d = R.read_json(C.home() / ".cursor" / "mcp.json", {})
        line(all(n in d.get("mcpServers", {}) for n in cfg.get("owned", {}).get("cursor_servers", [])), "cursor mcp.json")
    if "codex" in cfg.get("clients", []):
        import tomllib
        try:
            t = tomllib.loads((C.home() / ".codex" / "config.toml").read_text(encoding="utf-8"))
            line(all(n in t.get("mcp_servers", {}) for n in cfg.get("owned", {}).get("codex_servers", [])), "codex config.toml")
        except (OSError, tomllib.TOMLDecodeError) as e:
            line(False, "codex config.toml", str(e))
    if "kimi" in cfg.get("clients", []):
        import tomllib
        try:
            t = tomllib.loads((C.kimi_home() / "config.toml").read_text(encoding="utf-8"))
            ours = [h for h in t.get("hooks", []) if "agent_ops" in h.get("command", "")]
            denies = [r for r in t.get("permission", {}).get("rules", []) if r.get("decision") == "deny"]
            line(any(h.get("event") == "PreToolUse" and "guard" in h["command"] for h in ours) and bool(denies),
                 "kimi config.toml", f"{len(ours)} hooks, {len(denies)} deny rules")
        except (OSError, tomllib.TOMLDecodeError) as e:
            line(False, "kimi config.toml", str(e))
        d = R.read_json(C.kimi_home() / "mcp.json", {})
        line(all(n in d.get("mcpServers", {}) for n in cfg.get("owned", {}).get("kimi_servers", [])), "kimi mcp.json")
        print("note kimi: Kimi Code hooks fail open (a guard that crashes or times out lets the call through); the static "
              "deny rules and your read-only credentials are the backstop. Session notes aren't shown in Kimi (its "
              "SessionStart hook can't add text).")
    # Task board token (Plane API; other trackers are checked by their adapter on the agent host)
    core = next(p for p in plugins if p.name == "core")
    key, url = C.option_value(cfg, core, "tracker_api_key"), C.option_value(cfg, core, "tracker_base_url")
    if key and url and not os.environ.get("AGENT_OPS_OFFLINE"):
        import urllib.request
        try:
            req = urllib.request.Request(f"{url.rstrip('/')}/api/v1/users/me/", headers={"X-API-Key": key})
            with urllib.request.urlopen(req, timeout=15) as r:
                me = json.loads(r.read())
            line(True, "task board", f"signed in as {me.get('email', '?')}")
        except Exception as e:  # noqa: BLE001
            line(False, "task board", f"{e} (VPN on? token right?)")
    # MCP servers start and answer
    if not a.quick:
        for p in plugins:
            for name, spec in p.servers.items():
                if M.native_url(spec):
                    line(True, f"mcp {name}", "remote (log in from the client)")
                    continue
                r = M.resolve(p, name, cfg)
                if r["missing"]:
                    line(False, f"mcp {name}", "not set: " + ", ".join(r["missing"]))
                    continue
                exe = M.program(r["command"])
                good, detail = handshake([exe] + r["args"], {**os.environ, **r["env"]})
                line(good, f"mcp {name}", detail if good else detail + " (tunnel/VPN up? see SETUP.md)")
    return 0 if ok else 1


def cmd_uninstall(a):
    cfg = C.load_config()
    clients = a.client or cfg.get("clients", [])
    owned = cfg.get("owned", {})
    for c in clients:
        getattr(R, f"uninstall_{c}")(owned)
        print(f"removed agent-ops from {c}")
    cfg["clients"] = [c for c in cfg.get("clients", []) if c not in clients]
    if not cfg["clients"]:
        R.remove_skills()
        print("removed agent-ops skills from ~/.agents/skills (secrets stay in the keychain: `agent-ops secrets` lists them)")
    C.save_config(cfg)


def main():
    ap = argparse.ArgumentParser(prog="agent-ops", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="install or reconfigure for Cursor/Codex/Kimi")
    s.add_argument("--client", action="append", choices=C.CLIENTS)
    s.add_argument("--role", choices=C.ROLES)
    s.add_argument("--person", help="your key in people.yaml (e.g. noor)")
    s.add_argument("--tools", "--tool", dest="tool", action="append", metavar="NAMES",
                   help="tool plugins for your systems, comma-separated or repeated: gitlab, github, postgres, "
                        "clickhouse, jenkins, figma, metabase (with or without -tools); replaces the saved list")
    s.add_argument("--set", action="append", metavar="PLUGIN.OPTION=VALUE", help="a non-secret setting")
    s.add_argument("--non-interactive", action="store_true", help="don't ask for missing values")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("secrets", help="list settings (set / not set) or set one")
    s.add_argument("action", nargs="?", choices=["list", "set"], default="list")
    s.add_argument("name", nargs="?")
    s.set_defaults(fn=cmd_secrets)
    s = sub.add_parser("check", help="verify the setup")
    s.add_argument("--quick", action="store_true", help="skip starting the MCP servers")
    s.set_defaults(fn=cmd_check)
    s = sub.add_parser("update", help="pull the latest team setup and re-render")
    s.add_argument("--hook", choices=C.CLIENTS, help=argparse.SUPPRESS)
    s.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(fn=cmd_update)
    s = sub.add_parser("uninstall", help="remove agent-ops from a client")
    s.add_argument("--client", action="append", choices=C.CLIENTS)
    s.set_defaults(fn=cmd_uninstall)
    s = sub.add_parser("mcp", help=argparse.SUPPRESS)       # used by the client configs
    s.add_argument("ref")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=lambda a: M.launch(a.ref, a.dry_run))
    s = sub.add_parser("guard", help=argparse.SUPPRESS)     # used by the client hooks
    s.add_argument("--client", choices=C.CLIENTS, required=True)
    s.set_defaults(fn=lambda a: G.run(a.client))
    s = sub.add_parser("journal", help=argparse.SUPPRESS)   # used by the client hooks
    s.add_argument("--client", choices=C.CLIENTS, required=True)
    s.add_argument("--phase", choices=["pre", "post"], required=True)
    s.set_defaults(fn=lambda a: G.run_journal(a.client, a.phase))
    # Windows consoles default to a legacy code page (cp1252…) that can't print "→" and friends.
    # Hook input (stdin) is JSON in UTF-8 too.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)
