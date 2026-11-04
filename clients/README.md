# agent-ops for Cursor, Codex and Kimi Code

Claude Code users install the team's plugins from the agent-ops marketplace (`/plugin marketplace
add <your agent-ops repo>`). Cursor, Codex and Kimi Code users get the **same** rules, skills, MCP
servers, safety guard and action journal through this installer. The plugins in `plugins/` stay the
single source of truth; `agent-ops` renders them for each client. Works on macOS, Linux and Windows.

```bash
git clone <your agent-ops repo URL> ~/.agent-ops/repo
uv tool install --editable ~/.agent-ops/repo/clients
agent-ops setup --client cursor --role frontend --person noor   # and/or --client codex / --client kimi
agent-ops check
```

[SETUP.md](../SETUP.md) walks a whole team through this (and the agent host) in one go.

## What it writes

| | Cursor | Codex | Kimi Code |
|---|---|---|---|
| Skills (core + role + dependencies) | `~/.agents/skills/<name>` (all three read it) | same | same |
| Always-on team rules | `~/.cursor/plugins/local/agent-ops` (one `alwaysApply` rule) | block in `~/.codex/AGENTS.md` | block in `~/.kimi-code/AGENTS.md` |
| MCP servers | `~/.cursor/mcp.json` | block in `~/.codex/config.toml` | `~/.kimi-code/mcp.json` |
| Guard hooks | `beforeShellExecution` + `beforeMCPExecution` in `~/.cursor/hooks.json` (`failClosed`) | `PreToolUse` (shell + MCP) in `~/.codex/hooks.json` | `[[hooks]]` `PreToolUse` in a managed block of `~/.kimi-code/config.toml` |
| Journal hooks (every write is recorded) | `before*`/`after*` (never blocking) | `PreToolUse` / `PostToolUse` | `PreToolUse` / `PostToolUse` |
| Static deny rules (second layer) | — | — | `[[permission.rules]]` `deny` in the same block |
| Update hook | `sessionStart` | `SessionStart` (startup, resume) | `SessionStart` (startup, resume) |

Kimi Code's data root is `$KIMI_CODE_HOME` (default `~/.kimi-code`, on Windows
`C:\Users\<name>\.kimi-code`).

Only agent-ops' own entries are added, replaced or removed. The person's other MCP servers, hooks,
skills and text stay untouched. A name clash (e.g. their own `figma` server, or a skill folder
called `handoff`) is reported and ours is skipped. Each file is backed up once as
`<file>.agent-ops.bak`. `agent-ops uninstall [--client …]` removes everything we added.

## Secrets

`agent-ops setup` asks for each plugin option of the role (the same `userConfig` as in Claude
Code). Secrets are typed with hidden input and stored in the **OS keychain** (macOS Keychain,
Windows Credential Manager, Linux Secret Service via `keyring`; service `agent-ops`, account
`<plugin>.<option>`). Non-secret values are in `~/.agent-ops/config.json`. The MCP entries in the
client configs contain no values at all: each server starts as `<python> -m agent_ops mcp
<plugin>/<server>`. That launcher reads the plugin's `.mcp.json`, fills `${user_config.*}` from the
config file and keychain, and starts the real server with the values in its environment.
- **Figma** (OAuth, no headers) is written as a plain remote server, and each client does its own
  browser login (Codex: `codex mcp login figma`; Kimi: `/mcp-config login figma`).
- **agent-mcp** (a Bearer header with the person's task-board token) runs through `mcp-remote`
  over stdio. The header is passed as an environment variable that mcp-remote expands itself.

`agent-ops secrets` lists set / not set. `agent-ops secrets set <plugin>.<option>` changes one.

## Guard

The hooks run `python -m agent_ops guard --client <cursor|codex|kimi>`. It turns the client's hook
input into Claude Code's PreToolUse shape and runs the unchanged `plugins/core/scripts/guard.sh` with
the person's role, so the rules are identical in every client (including the DevOps exemption).
- **Cursor:** answers `allow` or `deny`; the hook is `failClosed`.
- **Codex and Kimi:** block with exit 2 and the reason.
- **Failure inside the adapter** (no bash, broken install): the call is blocked.
- **Windows:** the guard needs Git for Windows' `bash.exe` (Kimi: `KIMI_SHELL_PATH` if set); WSL's
  bash is never used.
- **Codex trust:** Codex runs user hooks only after the person trusts them once with `/hooks`.
- **Not covered:** file edits (Codex `apply_patch`, Kimi `Write`/`Edit`) aren't guarded, same as
  Claude Code's file edits.

### What Kimi Code can't do (and the mitigations)
- **Hooks fail open.** If the hook process crashes, can't start or times out, Kimi lets the call
  through (exit 2 is the only block). Mitigations: `[[permission.rules]]` `deny` rules for merges,
  deployments, database writes, direct DB clients and force-push (and, for non-DevOps roles,
  pipeline and CI runs), which the CLI enforces itself; read-only credentials; and a note in
  `agent-ops check`. Deny rules match MCP tools by **name only**, so argument-level rules (SQL
  write detection, push to `main`, production targets) live only in the guard hook.
- **Rule order.** Kimi applies the first matching permission rule. An `allow` rule of yours that
  comes earlier (e.g. `mcp__gitlab__*`) wins over our `deny` rules: keep broad allow rules out, or
  put them after the agent-ops block.
- **No session notes.** Kimi's `SessionStart` hook can't add text to the session, so the team's
  recurring-lesson / directive note (shown in Claude Code, Cursor and Codex) isn't shown; the
  update itself still runs.
- **Hook fields.** `[[hooks]]` entries may only have `event`, `matcher`, `command`, `timeout`.
  Keep the managed block intact; keys you add right after its end marker would belong to our last
  table.

## Updates

On session start, the update hook checks at most once an hour:
1. It runs `git pull --ff-only` in `~/.agent-ops/repo`.
2. If anything changed, it re-renders skills, rules and MCP servers. The changes apply from the
   next session.
3. If a new required setting appeared, the agent is told to ask the person to run `agent-ops setup`.

It never blocks or fails a session. To update by hand: `agent-ops update`.

## Develop / test

```bash
uv run --project clients python tests/clients.test.py    # offline, throwaway home folder
```

`AGENT_OPS_USER_HOME` and `AGENT_OPS_SECRETS_FILE` redirect the home folder and the keychain, and
`AGENT_OPS_OFFLINE=1` skips git and network calls. Real-client testing: [CHECKLIST.md](CHECKLIST.md).
