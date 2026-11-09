# Infrastructure

## The agent host

Any always-on machine you control: a small Mac (launchd) or a Linux server or VM (systemd). It runs
the service agents, agent-mcp, the wake listener, optionally a self-hosted Plane and n8n, and keeps
read-only clones of your repositories. Requirements: git, curl, jq, Python 3.9+, `uv`, Node.js
(for `npx`-based MCP servers), Docker (Plane, n8n and the nightly test containers), and the Claude
Code CLI signed in with an API key or a long-lived token (`claude setup-token`).

### Paths (the same on every host)

| Path | What |
|---|---|
| `/srv/agent-ops` | a clone of this repo's `main`, pulled before every tick (on macOS a synthetic link: `infra/macos/srv-link.sh`) |
| `/srv/repos/<name>` | read-only clones of the repos in `routing.yaml`, refreshed by `repo-sync.sh` |
| `/srv/nightly/<date>` | one folder per nightly pass |
| `~/.config/agent-ops/env` (mode 600) | the env file: every token and URL (names in `infra/env.template`) |
| `~/.config/agent-ops/revert.env` (mode 600) | revert tokens, read only by `journal revert-apply`, never loaded by any agent |
| `~/.local/share/agent-mcp/` | agent-mcp's SQLite, the journal database, answer quality counts |
| `~/.local/share/agent-ops/lineage.json` | the lineage cache |
| `~/.local/share/agent-ops/jenkins-watch/`, `~/.local/share/agent-ops/telegram/` | the Jenkins watcher's state and the watched Telegram topic's messages (optional) |
| log root `/<agent>/` | run logs (`~/Library/Logs/agent-ops` on macOS, `/var/log/agents` on Linux) |

Claude Code ignores a project's allow lists and hooks in a folder that isn't trusted, so the
service agents run only inside the trusted `/srv/agent-ops` checkout.

### macOS (launchd)

`infra/macos/install.sh` renders the plists from `infra/macos/*.plist.template` into
`~/Library/LaunchAgents` and loads them: one job per service agent (`StartInterval`), nightly and
its morning send (calendar intervals), agent-mcp and the wake listener (`KeepAlive`), backups, and
SSH tunnels if you need them. Flags choose what to load (`ENABLE_TIMERS`, `NIGHTLY`, `WAKE`,
`AGENT_MCP`, `JENKINS_WATCH`). The machine must not sleep (`pmset`).

### Linux (systemd)

`infra/bootstrap.sh` (idempotent) installs packages, creates the `agents` user, the paths and
`/etc/agent-ops/env`, and installs `infra/systemd/agent@.service` (oneshot, `NoNewPrivileges`,
`ProtectSystem=full`, write access only to the log, lock, agent-ops, repos and home paths) with one
timer per agent, plus services for agent-mcp and the wake listener. `JENKINS_WATCH=true` adds the
`agent-jenkins-watch.timer` and the Telegram poller (`telegram-poll.service` + `.timer`, every
minute, started at once).

## Adapters: task board and code host (`infra/adapters/`)

Everything a script or agent-mcp does with the task board or the code host goes through two
interfaces (Python standard library only; the full method list is in `infra/adapters/README.md`):

- **TrackerAdapter**: identity (`whoami`), projects and memberships, states, labels, members,
  items (list, get by id or key, update), comments, links, activity. Built in: **Plane** (Cloud or
  self-hosted Community Edition). Configured by `routing.yaml` (`tracker: plane`) and
  `TRACKER_BASE_URL`, `TRACKER_WORKSPACE`, `TRACKER_*_TOKEN`.
- **CodeHostAdapter**: merge requests (GitLab MRs or GitHub pull requests), changed files, commits,
  commit statuses, closing and restoring merge requests, deleting notes, clone URLs. Built in:
  **GitLab** (gitlab.com or self-managed) and **GitHub** (github.com or Enterprise). Each repo in
  `routing.yaml` names a `code_hosts` key; tokens are `CODEHOST_<KEY>_TOKEN` (read-only) and
  `CODEHOST_<KEY>_RELEASE_TOKEN` (statuses and notes).

Shell scripts call `infra/bin/adapter tracker|codehost …` (JSON out; tokens only from the env file).
Agents themselves use MCP servers for the same systems (the Plane MCP server, the GitLab or GitHub
MCP server). Adding Jira, Linear or GitHub Issues is one adapter file plus an MCP server entry.

**Plane Community Edition** lacks some APIs of Plane Cloud: `plugins/core/scripts/plane-mcp-ce.py`
reroutes the MCP server's relation calls to the endpoints CE has, and starts a pinned
`plane-mcp-server`.

## agent-mcp (`infra/agent-mcp/`)

The FastMCP server (section 03) listens on `127.0.0.1:8765`; publish it through your reverse proxy
at `AGENT_MCP_PUBLIC_URL` (people's plugins use that as `agent_mcp_url`). `/healthz` is open;
everything else needs a task-board token. SQLite in its data folder holds conversations, tickets
(14 days) and the message log (90 days, no text).

## Action journal (`infra/journal/`)

Every write any agent makes is recorded with its author, the value before and how to undo it.

- **Recording.** The core plugin's `journal.sh` hook (`PreToolUse` and `PostToolUse`) is in
  people's plugins, the service agents' settings and the Cursor/Codex/Kimi hooks. It sends only
  write-looking calls to agent-mcp's `POST /journal` with the caller's task-board token, never blocks
  or prints, and spools when agent-mcp is unreachable. A token whose email is in `people.yaml` is
  that person's agent; any other is a service agent (named by `X-Agent-Name` and `X-Agent-Run`).
- **Storage.** `infra/journal/journal.py` (standard library; also the CLI `infra/bin/journal`)
  keeps `events` (who, how, what, before, after, reversible, hint), `pending` snapshots and `plans`
  in `journal.sqlite3`. Before a task-board or code-host write it reads the item through the adapter
  as it is; after, as it is now. Telegram text is never stored. Host scripts record their own writes
  with `journal.py record` (commit statuses: the old state; alerts: irreversible).
- **Reading.** agent-mcp tools `blame` (merged with the task board's activity, so changes made by
  hand show as "by hand"), `suspects` (recent commits and journal events that touch a table, file,
  route or page, including what it is built from through lineage) and `revert_plan`.
- **Reverting.** `revert_plan` is a dry run that saves a plan `rv-…`. A person applies it in a
  terminal on the host: `infra/bin/journal revert-apply rv-…` (type the plan id; it refuses without a
  terminal). A service agent's own change is undone with that agent's own bot token; everything
  else with a person's tokens in `revert.env` (`TRACKER_REVERT_TOKEN`, `CODEHOST_<KEY>_REVERT_TOKEN`).
  Auto steps: item fields, labels and assignees (a field changed again since is left alone),
  created items → Cancelled, comments, links, merge requests closed or restored, notes, commit
  statuses. Manual steps come with the exact undo (git, CI, BI, design files, artifacts). Messages
  can't be unsent. The revert itself is journaled.
- **Guard rule 9:** agents never run `revert-apply` or `record`, and never touch the database file.

## Lineage (`infra/lineage/`)

A graph of table → view → API route → page → metric, built from what exists, cached a day
(`lineage build`, CLI `infra/bin/lineage`, agent-mcp tool `lineage`). Its scanners:

- **SQL directories**: each `.sql` file's CREATE/INSERT target (or a default database plus the file
  name) and the db-qualified tables it reads; `_stage` names count as production.
- **Warehouse views**: views and their SELECT from ClickHouse's `system.tables`, read with the
  agents' read-only user.
- **Backend routes**: router prefix + method path → controller → service methods (following
  private methods) → repository → the views in its SQL. The built-in scanner reads Elysia/Express-
  style TypeScript; add a scanner for your framework.
- **Frontend pages**: the router's paths → page folder → API path literals → the backend route.
- **By hand**: metrics (what they're computed from, where they're shown), page names, owners.

Queries: `impact <node>` (what changes if it changes), `upstream <node>` (what it's built from),
`files <paths> --impact` (a merge request's reach), `check` (hand-file names that no longer exist).

## Quality and data-trap checks (`infra/quality/`)

- **Claim measure** (`claims.py`): counts the factual claims in an analysis (fact parts only) or an
  agent-mcp answer, and those that cite nothing checkable (a file and line, an MR, a commit, an item
  key, an artifact, a build, a BI card, a URL, SQL). Deterministic, no model calls. Results go to the
  analyst's run log and to `answers-quality.jsonl`; the reference page shows the rates.
- **Data-trap checks** (`plugins/core/skills/data-traps/checks.yaml`, runner `traps.py`, CLI
  `infra/bin/traps`): read-only SELECTs that each return one number, with `expect`, `severity`,
  `action` (`block` | `notify`) and the `tables` they read. `traps run` runs them (nightly);
  `traps for-files <paths>` runs those whose tables the files reach through lineage (release). A
  check that errors is `unreadable`, never a pass. The shipped checks are examples on sample tables;
  write your own from your team's known traps.
- **Trap baseline** (`knowledge/trap-baseline.yaml`): known violations, `<check id>: {value,
  measured, note}`. A failing check no further from `expect` than its entry is `baseline` (listed
  in `baselined`, never blocking); worse is a normal fail; a check that passes again while it still
  has an entry is listed in `stale_baseline` (remove the entry in the fix's MR). `traps baseline
  [--ids …]` prints entries for the failing checks for a person to review. Only people edit it
  (a protected path). `AGENT_TRAP_BASELINE` points elsewhere.
- **MR scan** (`mr_scan.py`, CLI `infra/bin/mr-scan <repo-path> <iid>`): reads a merge request's
  diff through the code-host adapter (`merge_request_diffs`, GitLab or GitHub, the release token
  of the repo's host) and prints JSON: `removed` (routes with the router's prefix from the clone in
  `/srv/repos`, DTO fields, permission and role checks, auth plugins; an item that an added line
  brings back, or for some kinds any file, isn't removed) and `added` (SQL text built by
  interpolation). Patterns: `knowledge/contract-patterns.yaml` (an example for a TypeScript HTTP
  backend; protected). A diff that can't be read is `error`, never clean.
- **Waivers** (`waivers.py`, CLI `infra/bin/waivers`): `add --mr <repo>!<iid> --id <id> --author
  <username> --reason "…"` accepts or refuses a person's waiver (people from `people.yaml`
  `code_host_usernames`; service accounts from `service` and `AGENT_BOT_USERNAMES` are refused; a
  specific, non-boilerplate reason not copied from another MR; at most 3 per MR); `list --mr`;
  `repeated [--days 30] [--min 3]` for nightly. Ledger: `AGENT_WAIVERS_FILE` (default
  `~/.local/share/agent-ops/waivers.jsonl`).
- **Run signals** (`run_signals.py --hours 24`): rework in the service agents' runs for the nightly
  report (section 03, nightly).
- **Reference check** (`refcheck.py`, run by `tools/lint.sh`): every repo path, script and skill
  the skills, agent prompts, reference sections and `docs/*.md` name must exist (placeholders,
  per-machine files and other repos' paths are skipped); protected paths, trap baseline ids and
  edit-check ids are checked, and the plugin versions in a reference table must match each
  `plugin.json`. Prints `refcheck: clean` or one line per problem.

## Notifications: n8n and Telegram (`infra/n8n/`)

Optional. A task-board webhook goes to an n8n flow that (1) wakes triage, and (2) batches changes
into one private Telegram message per person after a few minutes, never to the person who made the
change, with quiet hours and a morning digest. A per-minute job reads the change from the board's
activity (webhooks arrive one per field, in parallel). `telegram-chat-ids.sh` fills people's chat
ids after they press Start on the bot. The flow is generated from `people.yaml`: rebuild it after
changes (`build-flow.sh --import`).

### Jenkins watcher's Telegram poller (`infra/telegram/`)

Optional (`JENKINS_WATCH=true`). `poll.py` reads the team bot's updates every minute with
getUpdates (outbound only, no webhook) and keeps the messages of the one watched chat
(`TELEGRAM_WATCH_CHAT_ID`) for 30 days in `~/.local/share/agent-ops/telegram/messages.sqlite3`, so the
Jenkins watcher can reply to a CI notifier bot's alerts. Other updates are dropped; `poll.py chats`
lists the groups and topics the bot has seen (ids, titles, bot usernames) for setup. It consumes
the updates `telegram-chat-ids.sh` also reads: stop it while you run that script.

## Self-hosted Plane (`infra/plane/`)

Optional, if you don't use Plane Cloud: `install-*.sh` runs a pinned Plane Community Edition in
Docker; `configure.sh` (idempotent, never deletes) creates the states, labels and members of each
project from `routing.yaml`; `subpath/` serves Plane under a path prefix behind a proxy; `bi/`
exposes the board to a BI tool through a read-only database user.

## Other host scripts (`infra/bin/`)

| Script | What |
|---|---|
| `run-agent.sh` | one tick of a service agent (section 03) |
| `precheck.sh` | "anything to do?" per agent, without a model |
| `repo-sync.sh` | clone/fetch the read-only repo clones with the agents' read-only tokens |
| `post-commit-status.sh` | the release agent's commit status (GitLab or GitHub), recorded in the journal |
| `alert.sh` | private Telegram alert to the question owner, always logged |
| `nightly.sh`, `nightly-send.sh` | the nightly pass and its morning messages |
| `figma-shot.py` | render a design frame read-only and attach it to an item |
| `adapter` | the adapters' CLI |
| `journal`, `lineage`, `traps` | CLIs for the journal, lineage and trap checks |
| `mr-scan`, `waivers` | the release agent's MR scan and waiver ledger (above) |

`infra/macos/backup.sh` backs up the env files, agent-mcp's data and (if self-hosted) Plane's
database daily, keeping a week.

## The env file

`infra/env.template` lists every name with a comment. Groups:

| Group | Names |
|---|---|
| Claude | `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`; `AGENT_MODEL_<AGENT>`, `AGENT_MAX_TURNS_<AGENT>`, `AGENT_BUDGET_5H_USD`, `AGENT_BREAKER_FAILS`, `AGENT_BREAKER_COOLOFF_S`, `AGENT_MAX_IDLE_HOURS` |
| Task board | `TRACKER_BASE_URL`, `TRACKER_WORKSPACE`, `TRACKER_TRIAGE_TOKEN` (triage, analyst, agent-mcp), `TRACKER_RELEASE_TOKEN`, `TRACKER_NIGHTLY_TOKEN` (or `_FILE`), `TRACKER_ADMIN_TOKEN` (setup only) |
| Code hosts | per `code_hosts` key: `CODEHOST_<KEY>_TOKEN` (read-only), `CODEHOST_<KEY>_RELEASE_TOKEN` |
| Data | `PG_RO_URL`, `CH_RO_HOST`, `CH_RO_PORT`, `CH_RO_SECURE`, `CH_RO_USER`, `CH_RO_PASSWORD`, `METABASE_URL`, `METABASE_AGENT_API_KEY`, `JENKINS_MCP_URL`, `JENKINS_RO_AUTH`, `FIGMA_RO_TOKEN` |
| Notify | `TELEGRAM_BOT_TOKEN`, `ALERTS`, `WAKE_TOKEN` |
| Jenkins watcher (optional) | `TELEGRAM_WATCH_CHAT_ID`, `TELEGRAM_WATCH_TOPIC_ID`, `TELEGRAM_BOT_USERNAME`, `JENKINS_WATCH_STUCK_FACTOR`, `JENKINS_WATCH_STUCK_MIN` (plus `JENKINS_MCP_URL`, `JENKINS_RO_AUTH`) |
| Paths and URLs | `AGENT_OPS_ROOT`, `AGENT_REPOS_DIR`, `AGENT_LOG_ROOT`, `AGENT_MCP_PUBLIC_URL` |

Every credential is read-only except the release agent's code-host token. Revert tokens never go
in this file.

## Where logs and state live

| What | Where |
|---|---|
| run logs, `paused`, `fail-count`, `skipped.log`, `last-run-at` | `<log root>/<agent>/` |
| alerts | `<log root>/alerts.log` |
| run transcripts | `~/.claude/projects/*agent-ops-service-<agent>/` |
| conversations, tickets, message log | `~/.local/share/agent-mcp/agent-mcp.sqlite3` |
| the journal | `~/.local/share/agent-mcp/journal.sqlite3` |
| answer quality counts | `~/.local/share/agent-mcp/answers-quality.jsonl` |
| lineage cache | `~/.local/share/agent-ops/lineage.json` |
| nightly passes | `/srv/nightly/<date>/` |
| Jenkins watcher: incidents, message ids, last facts and decisions | `~/.local/share/agent-ops/jenkins-watch/` |
| the watched Telegram topic's messages (30 days) | `~/.local/share/agent-ops/telegram/messages.sqlite3` |
