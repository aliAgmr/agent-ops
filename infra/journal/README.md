# Action journal: who changed what, and how to undo it

Every write any agent makes, whether a person's Claude Code, Cursor, Codex or Kimi or a service agent, is
recorded with its author, the value before and the way to undo it. Three questions get answers:

| Question | agent-mcp tool | CLI on the agent host |
|---|---|---|
| Who changed BIL-123 / this MR or PR, and how? | `blame(target)` | `infra/bin/journal blame BIL-123` |
| What recent change broke this table / page / route? | `suspects(project, term)` | `infra/bin/journal suspects BIL analytics.orders --days 14` |
| What would undoing this run / session / change do? | `revert_plan(...)` (dry run) | `infra/bin/journal revert-plan --run triage-…` |
| Undo it | (never through an agent) | `infra/bin/journal revert-apply rv-…` (a person, in a terminal) |

`infra/bin/journal show --actor noor --since 1d` lists raw events.

## How a write gets recorded

```
agent tool call ──▶ PreToolUse  journal.sh pre  ──POST /journal──▶ agent-mcp: save the item as it is
                ──▶ (the write)
                ──▶ PostToolUse journal.sh post ──POST /journal──▶ agent-mcp: save it as it is now,
                                                                     record one event
host scripts (post-commit-status.sh, alert.sh) ──▶ journal.py record ──▶ the same database
  (code-host writes: `record --system codehost --host <code_hosts key> …`)
```

- **The hook** (`plugins/core/scripts/journal.sh`) is in the core plugin (people's Claude Code), in
  each service agent's `.claude/settings.json`, and in the Cursor/Codex/Kimi hooks (`agent_ops journal`).
  It sends only calls that look like writes and authenticates with the person's task-board token (service
  agents: their bot's token, set by `run-agent.sh` with `AGENT_NAME` and `AGENT_RUN_ID`). It never
  blocks or prints anything. If agent-mcp is unreachable, `post` records wait in
  `~/.cache/agent-ops/journal-spool.jsonl` (or the plugin data folder) and go with the next one.
- **Who:** a token whose task-board email is in `people.yaml` is that person's agent, whatever headers it
  sends. Any other token is a service agent, named by `X-Agent-Name` and `X-Agent-Run`.
- **agent-mcp** (`infra/agent-mcp/server.py`) answers `POST /journal` and `/journal/batch` behind the
  same token check as MCP. It calls `journal.py` (`Journal.ingest`), which classifies the call and
  drops reads. It reads the item before and after through the adapters (`infra/adapters`, reached
  through `journal.Connector`): task-board items with the caller's token, and GitLab MRs / GitHub
  PRs with the agents' read token for that code host (`CODEHOST_<KEY>_TOKEN`; the key comes from
  the MCP server's name and `routing.yaml` → `code_hosts`). Everything is stored in
  `~/.local/share/agent-mcp/journal.sqlite3` (WAL), with secrets redacted. Telegram messages keep
  who and whom only, never the text.
- **Visibility:** `blame`, `suspects` and `revert_plan` show events in projects you are a member of,
  your own events, and everything to approvers and the question owner (`routing.yaml`).

## What can be undone

| Write | Undo | How |
|---|---|---|
| Task-board item: fields, labels, assignees | auto | puts back each changed field; a field changed again since is left alone and reported |
| Task-board item created | auto | moved to the cancelled state with a `[revert]` comment (never deleted) |
| Task-board comment created / edited | auto | deleted / old text restored |
| Task-board link created | auto | removed |
| GitLab MR / GitHub PR, or an issue, created | auto | closed |
| GitLab MR / GitHub PR, or an issue, edited | auto | title, description, labels, target branch restored |
| MR / PR / issue comment or thread | auto | note deleted |
| `release-agent` commit status | auto | the previous state posted again |
| git push / commit, `glab` / `gh`, `curl` writes, ssh, docker | manual | the plan says how (`git revert` in an MR/PR, …) |
| Jenkins run / config, Metabase, Figma, artifacts | manual | abort / restore from `~/jenkins-backups`, revision history, version history, republish the previous version |
| Telegram messages | no | can't be unsent: send a correction |
| Failed writes | none | recorded, never planned |

## Reverting

1. Plan it. Anyone's agent can call `revert_plan(run=…)`, or run
   `infra/bin/journal revert-plan --run <id>` on the host. Nothing changes yet: the plan is saved
   with an id (`rv-…`) and lists `auto` and `manual` steps.
2. Apply it. Only a person can do this, in a terminal on the agent host:
   `infra/bin/journal revert-apply rv-…`. It shows the steps and asks you to type the plan id.
   Tokens, per step:
   - a service agent's (or host script's) own change: that agent's own bot token from the env file
     (triage/analyst `TRACKER_TRIAGE_TOKEN`, release `TRACKER_RELEASE_TOKEN` /
     `CODEHOST_<KEY>_RELEASE_TOKEN`, nightly `TRACKER_NIGHTLY_TOKEN`), so undoing it needs no more
     rights than making it did;
   - everything else (people's agents): a person's tokens in `~/.config/agent-ops/revert.env`
     (mode 600; the command refuses a file others can read): `TRACKER_REVERT_TOKEN` and, per code
     host, `CODEHOST_<KEY>_REVERT_TOKEN` (GitLab: api scope; GitHub: a fine-grained token with
     pull requests / issues write). Only this command reads that file; `run-agent.sh`
     never loads it, so no agent holds these tokens. Without them, those steps fail and say so, and
     the rest still run. Each result names the token it used.
   - The CLI refuses to run without a terminal.
   - The guard (rule 9) blocks `revert-apply`, `record` and the database file for every agent.
3. The revert is journaled too (actor kind `revert`), and the undone events are marked
   `reverted_by`, so a second plan skips them.
4. Do the `manual` steps by hand.

## Not covered (yet)

- Edits to local files (git is their history).
- Writes by the nightly publish/send scripts (their own logs).
- Changes people make by hand. For task-board items, `blame` merges in the item's activity
  (tracker adapter `activities`), so these show as "by hand on the task board".
- A tracker other than Plane: its MCP server's write tools need classification rules in
  `journal.py` `classify()` (the adapter covers snapshots and reverts).
- Cursor's before/after hooks may not share an id. Then a Cursor write has no saved "before" and
  its revert step is manual.

Tests: `python3 tests/journal.test.py` (fake tracker and code-host adapters, a fake journal endpoint; the real hook; a
throwaway git repo), plus the journal section of `tests/agent-mcp/test_offline.py`.
