# Working on agent-ops (for coding agents and people)

This file is read by Claude Code (through `CLAUDE.md`), Codex, Kimi and Cursor. It covers changes to
this repository. It doesn't cover the service agents' daily work: they follow `service/<agent>/CLAUDE.md`
and `tick.md`. Desk sessions follow `desks/CLAUDE.md`.

## Before you open a pull request

- `bash tools/lint.sh` is clean. It checks JSON, shell scripts, skill front matter and the leak
  check.
- `bash tools/run-tests.sh` passes. Every test is offline and fakes the external API it needs.
- Each plugin you changed has a higher `version` in `plugins/<p>/.claude-plugin/plugin.json`.
- The reference docs (`docs/reference/sections/*.md`) describe the change. That covers a new
  component, behaviour, label, state, env variable, tool, permission, schedule or guard rule. A PR
  with no docs change says why.

## Keep it generic

agent-ops is used by many teams, so nothing in the repository may identify a real team.
- **Names:** examples use the example team (Alex, Sam, Riya, Noor, Kenji, Omar, Lena), company
  "Acme", projects BIL / PRC / ANL, repos `acme/*`, and hosts `*.example.com`.
- **Contact details:** emails are only `@example.com`. IPs are only loopback or the documentation
  ranges (192.0.2.x, 198.51.100.x, 203.0.113.x).
- **Secrets:** never in files, tests or examples. Tests use obvious fakes (`tok-test-…`).
- **Leak check:** `tools/leak-check.py` enforces this in CI. `tools/leak-terms.txt` only grows:
  add to it, never remove from it.

## The hard rules this code enforces

Credentials come first; the guard hook (`plugins/core/scripts/guard.sh`) is the second layer.
Agents never:
- write to a database;
- run CI or Jenkins jobs (DevOps may run non-production ones);
- merge, deploy or push to `main`;
- delete task-board items;
- edit the action journal.
A change that loosens a rule needs a test in `tests/guard.test.sh` and a line in `docs/security.md`.

## Layout
- `plugins/`: Claude Code plugin marketplace. `core` is for everyone; `role-*` adds per-role
  skills and MCP servers.
- `clients/`: renders the same plugins for Cursor, Codex and Kimi.
- `service/`: the unattended agents: triage, analyst, release, nightly, and the agent-mcp answerer.
- `infra/`: the agent host. Includes macOS launchd and Linux systemd, the tracker and code-host
  adapters, agent-mcp, the action journal, lineage, quality checks and nightly.
- `desks/`: long-running personal "desk" sessions (examples).
- `docs/`: product docs and the reference generator. `SETUP.md`: the one-go setup an agent follows.

## Mirrors
Some teams run a private fork of agent-ops and port changes both ways by hand. `SYNC.md` maps the
files that correspond. If you maintain such a fork, follow its rules there.
