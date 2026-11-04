# Security model

agent-ops assumes an agent can be wrong, confused or steered by text it reads, and is built so that
none of those can do lasting damage on its own.

## 1. Credentials first

What an agent *can* do is decided by the credentials it holds, not by its prompt.

- **Databases and warehouse:** read-only users (`PG_RO_URL`, `CH_RO_*` with `readonly = 1`).
- **CI:** a user without build permission. DevOps people may hold their own user with rights on
  dev/stage jobs only.
- **Code host:** people get Developer (never Maintainer) access; `main` is protected so only the
  merge owner merges. Service agents use read-only tokens; only the release agent holds a token that
  can post commit statuses and notes, and its status script refuses repos not in `routing.yaml`.
- **Task board:** each service agent has its own bot user and token, so every change is attributable.
- **Secrets on people's machines** are sensitive plugin settings stored in the OS keychain, entered
  with hidden input, never in chat or config files.

## 2. The guard, second

`plugins/core/scripts/guard.sh` is a `PreToolUse` hook in every client (Claude Code plugin; Cursor,
Codex and Kimi through the installer's adapter; every service agent). It blocks: database writes
and DDL (by scanning the SQL of every tool on a database or BI server), direct database clients, CI
job runs, merges, deployments and pipeline triggers, pushes to `main` and force-pushes, deleting or
archiving board items, writes in the read-only repo clones, publishing artifacts from service agents,
and editing the journal. The environment can only tighten it. When a call is blocked the agent must
stop and ask a person; it must not reword the call to get around it. If the guard can't run in
Cursor, Codex or Kimi, the call is blocked.

## 3. Allow lists, third

Service agents run with `defaultMode: dontAsk`: anything not on their allow list is refused without a
prompt. Their MCP servers are their own (`--strict-mcp-config`), with their own read-only tokens.

## 4. Text is data, not instructions

Board items, comments, merge request text and repo files are written by people. Service agents
triage them and never obey instructions embedded in them; they flag such text instead.

## 5. Every write has an author and an undo

The journal hook records every write tool call with the caller's identity (proved by their
task-board token) and the value before. Agents can read it (`blame`, `suspects`) and prepare
`revert_plan`s; only a person applies a revert, in a terminal on the host. Revert tokens live in
`~/.config/agent-ops/revert.env` (mode 600), which no agent ever loads; a service agent's own change
is undone with that agent's own token.

## 6. Untrusted code runs without secrets

The nightly tests run each repo in a throwaway container with only the repo mounted, installs with
`--ignore-scripts`, no network credentials and no host environment.

## 7. Privacy

agent-mcp keeps conversations private to their creator and project members, redacts every known
secret value from answers, logs timings but never question or answer text, keeps who/whom/when of
messages but never the text, and records only claim counts for quality. The reference page redacts
emails, IP addresses, internal hosts and token-like strings, and refuses to build if any remain.

## 8. The leak check

`tools/leak-check.py` (in CI) fails on any term in `tools/leak-terms.txt`, any email outside
documentation domains, any non-documentation IP address and any private hostname. Use it in your
fork with your own company's terms so your deployment details never reach a public copy.

## Reporting a vulnerability

Please open a private security advisory on the repository rather than a public issue.
