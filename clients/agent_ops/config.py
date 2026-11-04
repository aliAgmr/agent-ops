"""Where things live, the person's settings, their secrets, and the plugins their role uses."""
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROLES = ("product", "design", "backend", "frontend", "data", "devops")
CLIENTS = ("cursor", "codex", "kimi")
SECRET_SERVICE = "agent-ops"
WINDOWS = os.name == "nt"


def home() -> Path:
    # AGENT_OPS_USER_HOME lets the tests use a throwaway home folder.
    return Path(os.environ.get("AGENT_OPS_USER_HOME") or Path.home())


def kimi_home() -> Path:
    """Kimi Code's data root: $KIMI_CODE_HOME, else ~/.kimi-code (C:\\Users\\<name>\\.kimi-code on
    Windows). Tests (AGENT_OPS_USER_HOME) always use the throwaway home."""
    if os.environ.get("KIMI_CODE_HOME") and not os.environ.get("AGENT_OPS_USER_HOME"):
        return Path(os.environ["KIMI_CODE_HOME"]).expanduser()
    return home() / ".kimi-code"


def state_dir() -> Path:
    return home() / ".agent-ops"


def repo_root() -> Path:
    """The agent-ops checkout this package runs from (installed with `uv tool install -e`)."""
    return Path(__file__).resolve().parents[2]


def config_path() -> Path:
    return state_dir() / "config.json"


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    tmp = config_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, config_path())


# ---- secrets: the OS keychain (macOS Keychain, Windows Credential Manager) ----------------------
# AGENT_OPS_SECRETS_FILE (tests only) swaps the keychain for a JSON file.
def _file_store():
    p = os.environ.get("AGENT_OPS_SECRETS_FILE")
    return Path(p) if p else None


def get_secret(name: str):
    f = _file_store()
    if f:
        try:
            return json.loads(f.read_text(encoding="utf-8")).get(name)
        except (OSError, ValueError):
            return None
    import keyring
    return keyring.get_password(SECRET_SERVICE, name)


def set_secret(name: str, value: str) -> None:
    f = _file_store()
    if f:
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        data[name] = value
        f.write_text(json.dumps(data), encoding="utf-8")
        return
    import keyring
    keyring.set_password(SECRET_SERVICE, name, value)


def delete_secret(name: str) -> None:
    f = _file_store()
    if f:
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            data.pop(name, None)
            f.write_text(json.dumps(data), encoding="utf-8")
        return
    import keyring
    try:
        keyring.delete_password(SECRET_SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


# ---- plugins ------------------------------------------------------------------------------------
class Plugin:
    def __init__(self, root: Path):
        self.root = root
        meta = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.name = meta["name"]
        self.version = meta.get("version", "0")
        self.deps = meta.get("dependencies", [])
        self.options = meta.get("userConfig", {})
        mcp = root / ".mcp.json"
        self.servers = json.loads(mcp.read_text(encoding="utf-8")).get("mcpServers", {}) if mcp.exists() else {}
        sk = root / "skills"
        self.skills = sorted(p for p in sk.iterdir() if (p / "SKILL.md").exists()) if sk.exists() else []
        # Claude Code subagents (agents/*.md); Cursor/Codex/Kimi get them as skills of the same name.
        ag = root / "agents"
        self.agents = sorted(ag.glob("*.md")) if ag.exists() else []


def available_tools(repo: Path = None) -> list:
    """Tool plugins (MCP servers for one system each: gitlab-tools, github-tools, postgres-tools …)."""
    base = (repo or repo_root()) / "plugins"
    return sorted(p.name for p in base.iterdir() if p.name.endswith("-tools") and (p / ".claude-plugin" / "plugin.json").exists())


def tool_names(values) -> list:
    """--tools gitlab,clickhouse / --tool gitlab-tools → ["clickhouse-tools", "gitlab-tools"]."""
    out = []
    for v in values or []:
        for t in str(v).split(","):
            t = t.strip()
            if t:
                out.append(t if t.endswith("-tools") else f"{t}-tools")
    return sorted(set(out))


HOOK_OPTIONS = ("tracker_api_key", "tracker_base_url", "tracker_workspace_slug", "agent_mcp_url", "role")


def hook_env(cfg: dict) -> dict:
    """The CLAUDE_PLUGIN_OPTION_* variables Claude Code gives the core plugin's hooks (journal.sh,
    session-notes.sh), from this person's settings and keychain. Empty values are left out."""
    core = next((p for p in plugins_of(cfg) if p.name == "core"), None)
    env = {}
    if core is None:
        return env
    for key in HOOK_OPTIONS:
        v = option_value(cfg, core, key)
        if v not in (None, ""):
            env[f"CLAUDE_PLUGIN_OPTION_{key.upper()}"] = str(v)
    return env


def plugins_for(role: str, repo: Path = None, tools=()) -> list:
    """core + role-<role> + the person's tool plugins + their dependencies, dependencies first."""
    if role not in ROLES:
        raise SystemExit(f"unknown role {role!r}; one of: {', '.join(ROLES)}")
    base = (repo or repo_root()) / "plugins"
    unknown = [t for t in tools if not (base / t / ".claude-plugin" / "plugin.json").exists()]
    if unknown:
        raise SystemExit(f"unknown tool plugin(s) {', '.join(unknown)}; available: {', '.join(available_tools(repo))}")
    out, seen = [], set()

    def add(name):
        if name in seen:
            return
        seen.add(name)
        p = Plugin(base / name)
        for d in p.deps:
            add(d)
        out.append(p)

    add("core")
    add(f"role-{role}")
    for t in tools:
        add(t)
    return out


def plugins_of(cfg: dict) -> list:
    """The plugins this person's config selects (role + tools)."""
    return plugins_for(cfg.get("role", ""), repo_root(), cfg.get("tools", []))


def option_value(cfg: dict, plugin: Plugin, key: str):
    """A plugin option's value: core.role from the config, secrets from the keychain, others from
    the config or the plugin's default."""
    spec = plugin.options.get(key, {})
    full = f"{plugin.name}.{key}"
    if plugin.name == "core" and key == "role":
        return cfg.get("role")
    if spec.get("sensitive"):
        return get_secret(full)
    return cfg.get("values", {}).get(full, spec.get("default"))


def missing_options(cfg: dict, plugins: list) -> list:
    out = []
    for p in plugins:
        for key, spec in p.options.items():
            v = option_value(cfg, p, key)
            if spec.get("required") and (v is None or v == ""):
                out.append((p, key))
    return out


# ---- programs -----------------------------------------------------------------------------------
def find_bash():
    """bash for guard.sh and plugin scripts. On Windows: Git for Windows' bash (never WSL's
    C:\\Windows\\System32\\bash.exe, which runs in another filesystem). Kimi Code uses the same Git
    Bash; KIMI_SHELL_PATH, when set, names it."""
    if not WINDOWS:
        return shutil.which("bash") or "/bin/bash"
    candidates = []
    if os.environ.get("KIMI_SHELL_PATH"):
        candidates.append(Path(os.environ["KIMI_SHELL_PATH"]))
    git = shutil.which("git")
    if git:
        g = Path(git).resolve()
        for up in (g.parent.parent, g.parent.parent.parent):
            candidates += [up / "bin" / "bash.exe", up / "usr" / "bin" / "bash.exe"]
    for env in ("ProgramFiles", "ProgramW6432", "LocalAppData"):
        base = os.environ.get(env)
        if base:
            candidates += [Path(base) / "Git" / "bin" / "bash.exe", Path(base) / "Programs" / "Git" / "bin" / "bash.exe"]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def python_exe() -> str:
    """The interpreter of the installed agent-ops tool, used in hook and MCP commands."""
    return sys.executable


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
