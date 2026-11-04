# Real-client test checklist (Cursor / Codex / Kimi Code, macOS, Linux and Windows)

The offline tests cover rendering, the launcher and the guard adapter. They can't drive a real
Cursor, Codex or Kimi Code, so one teammate per platform runs this once, and again after changes to
`clients/`. Report each line as ok / failed with the exact message. Never include secret values.

Follow [SETUP.md](../SETUP.md) or the commands in `clients/README.md`.

## Install
- [ ] `agent-ops --help` works in a new terminal (after `uv tool update-shell`).
- [ ] `agent-ops setup …` asks only for your role's settings and hides secret input.
- [ ] `agent-ops secrets` shows every required value as `set`, with no secret printed.
- [ ] `agent-ops check` is all ok (data servers need the tunnel up).
- [ ] Windows: `agent-ops check` shows `bash (for the guard): C:\…\Git\…\bash.exe`, not System32.

## Cursor
- [ ] After a restart, Settings → MCP lists the task-board server (`plane`), `agent-mcp` and your role's servers as
      connected. `figma` logs in through the browser.
- [ ] Settings → Rules (or the plugin list) shows the agent-ops rule. Ask "what are your team
      rules?" and it should mention the task board, `task-protocol` and the hard rules.
- [ ] Skills: type `/` in the chat; `task-protocol` and `scope-guard` are listed.
- [ ] **Guard, shell:** in a scratch git repo, ask the agent to run `git push origin main`. It must
      be blocked with "BLOCKED by agent-ops guard …".
- [ ] **Guard, MCP:** ask it to delete a task-board item (any id). It must be blocked before the
      call.
- [ ] **Allow doesn't skip approval:** with Cursor set to ask before running commands, a harmless
      command (`ls`) must still ask you first. If it runs without asking, report it: the guard's
      "allow" answer would then override your approval setting.
- [ ] Windows: hooks run (the push test above). If Cursor says the hook failed, report the message
      from Settings → Hooks / output.
- [ ] Update: the next session after an agent-ops change shows a short "agent-ops updated" note (or
      run `agent-ops update`).

## Codex
- [ ] `codex` warns that hooks need review. `/hooks` lists the two agent-ops hooks; trust both.
- [ ] `/mcp` lists the servers, and `codex mcp login figma` works (if your role has Figma).
- [ ] `~/.codex/AGENTS.md` rules are applied: ask "what are your team rules?".
- [ ] Skills: `task-protocol` and `scope-guard` are listed (`/skills`).
- [ ] **Guard, shell:** `git push origin main` in a scratch repo is blocked with the guard's
      message.
- [ ] **Guard, MCP:** deleting a task-board item is blocked.
- [ ] Windows: the hook command runs (the push test above). If Codex can't start it, report the
      error. The command is `"<python.exe>" -m agent_ops guard --client codex` and may need a
      `command_windows` variant.
- [ ] Update note appears in the next session after a change.

## Kimi Code
- [ ] A new `kimi` session: `/mcp` lists the agent-ops servers as connected; `/mcp-config login figma` works (if your role has Figma).
- [ ] `~/.kimi-code/AGENTS.md` rules are applied: ask "what are your team rules?".
- [ ] Skills: `/skill:task-protocol` exists (Kimi reads `~/.agents/skills`).
- [ ] **Guard, shell:** `git push origin main` in a scratch repo is blocked with the guard's message.
- [ ] **Guard, MCP:** deleting a task-board item is blocked.
- [ ] **Deny rules:** a merge tool call (e.g. `merge_merge_request` / `merge_pull_request`) is refused even with the hook command temporarily broken (rename the python path in the block, try, restore).
- [ ] Windows: hooks run through Git Bash (the push test above); report the message if not.

## Remove
- [ ] `agent-ops uninstall` leaves your own MCP servers, hooks, permission rules, skills and AGENTS.md text as they
      were.
