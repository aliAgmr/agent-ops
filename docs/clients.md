# Supported clients

Every person keeps their own AI tool. Claude Code gets agent-ops as plugins; Cursor, Codex and Kimi
Code get the same content rendered by the `agent-ops` installer (`clients/`, details in
[`clients/README.md`](../clients/README.md)).

## Feature matrix

| | Claude Code | Cursor | Codex | Kimi Code |
|---|---|---|---|---|
| Team skills | yes (plugins) | yes (`~/.agents/skills`) | yes (`~/.agents/skills`) | yes (`~/.agents/skills`) |
| Always-on team rules | yes (plugin skills, CLAUDE.md) | yes (a local plugin, one `alwaysApply` rule) | yes (a block in `~/.codex/AGENTS.md`) | yes (a block in `~/.kimi-code/AGENTS.md`) |
| MCP servers (task board, code host, data, agent-mcp) | yes | yes (`~/.cursor/mcp.json`) | yes (`~/.codex/config.toml`) | yes (`~/.kimi-code/mcp.json`) |
| Secrets in the OS keychain, none in config files | yes | yes | yes | yes |
| Guard hook (shell + MCP) | yes | yes, fails closed | yes (after the person trusts hooks once with `/hooks`) | yes, but **fails open** if the hook can't run (see below) |
| Static deny rules (second layer) | allow lists (service agents) | — | — | yes (`[[permission.rules]]`, by tool name only) |
| Action journal hook | yes | yes (before/after shell and MCP) | yes (`PreToolUse` / `PostToolUse`) | yes (`PreToolUse` / `PostToolUse`) |
| Session notes (recurring lessons, open directives) | yes | yes | yes | **no** (Kimi's `SessionStart` hook can't add text) |
| Automatic updates | plugin version bumps | hourly at session start | hourly at session start | hourly at session start |
| File edits guarded | no (as in every client) | no | no (`apply_patch`) | no (`Write`/`Edit`) |

## Platforms

| | macOS | Linux | Windows |
|---|---|---|---|
| Claude Code plugins | yes | yes | yes (the guard needs Git for Windows' `bash.exe`) |
| `agent-ops` installer (Cursor, Codex, Kimi) | yes | yes (keychain via Secret Service) | yes (Credential Manager; Cursor hook commands run through PowerShell; never WSL's bash) |
| Agent host | yes (launchd) | yes (systemd) | no (use a small Linux VM or a Mac) |

## What Kimi Code can't do

- **Its hooks fail open.** If the hook process crashes, can't start or times out, Kimi lets the
  call through (only exit 2 blocks). agent-ops adds `[[permission.rules]]` deny rules for merges,
  deployments, database writes, direct database clients and force-push (and pipeline and CI runs for
  non-DevOps roles), which Kimi enforces itself. Those rules match MCP tools by name only, so the
  argument-level checks (SQL write detection, pushes to `main`, production targets) exist only in
  the hook. Read-only credentials remain the real boundary.
- **First matching permission rule wins.** A broad `allow` rule of the person's own placed before
  the agent-ops block overrides its deny rules.
- **No session notes:** recurring lessons and open directives aren't shown at session start.
- **Hook entries** may only use `event`, `matcher`, `command` and `timeout`.

`agent-ops check` reports these limits for Kimi users.
