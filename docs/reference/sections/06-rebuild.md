# Rebuild from scratch

This section is for someone (or an agent) who wants to understand why agent-ops is shaped the way
it is, or build something similar.

## The shape in one paragraph

One repository is the single source of truth for every agent of a team. A task board is the only
channel between agents and people. People's agents get the team's rules as plugins (or rendered
config for other clients) with a guard hook; unattended service agents run on a host from the same
repository, each a `claude -p` run in its own folder with its own allow list, MCP servers and bot
token, started by a scheduler only when a cheap precheck finds work. Read-only credentials are the
real safety boundary; the guard stops agents from even trying; the journal records every write so it
can be traced and undone.

## Build order

1. **Board and rules.** States, labels and the comment format (`task-protocol`); `routing.yaml`.
   Done when people use the board by hand with the states.
2. **People's plugins.** `core` with `task-protocol`, `scope-guard`, `handoff`; one role plugin.
   Done when one person's agent creates and moves tasks correctly.
3. **Guard and read-only credentials.** Read-only database users, a CI user without build rights,
   Developer-only code-host access; `guard.sh` with its tests. Done when every hard rule has a test.
4. **First service agent (triage)** with its own bot token, run by hand, then on a timer.
5. **Precheck and budget.** Idle ticks cost nothing; a broken check runs anyway; a forced run every
   few hours.
6. **More agents:** release (a status that gates merges), analyst (deep analysis before routing),
   nightly (boards in line with reality, findings, report).
7. **Q&A and messaging:** agent-mcp, so people's agents can ask about what they can't see.
8. **Journal, lineage, quality:** attribution and undo, the data map, measured output quality.
9. **Reports:** the reference page.

## Key design decisions and why

| Decision | Why |
|---|---|
| The task board is the only channel between agents | One auditable channel people also see; no extra infrastructure. To ask triage something, create an item. |
| One repository for everything | Prompts, skills, MCP configs, permissions, schedules, flows and setup in one place; moving hosts or accounts is a variable swap. |
| Read-only credentials first, guard second | Credentials are the real boundary; the hook stops the attempt and tells the agent to ask a person. |
| The guard checks every tool of a database/BI server, not a list of names | A tool renamed in a package update can't slip past. |
| The environment can only tighten the guard | Service agents never get a role exemption; dry-run and artifact flags only add blocks. |
| Precheck before tokens, fail open | Idle ticks cost nothing on a shared plan; a broken check must not stop the agents. |
| Model and turn cap per agent, budget from own logs | Agents share limits; a subscription token can't read plan usage, so spend is summed from the run logs. |
| Small batches, write after each item; one analysis per run | A crash leaves finished work behind; the "done" label is written last. |
| One bot and token per service agent | Changes are attributable; prechecks ignore the bots' own edits; the journal undoes a bot's change with its own rights. |
| Only the release agent holds a write-scoped code-host token | Everyone else stays read-only; the status script refuses repos not in `routing.yaml`. |
| People are found by email | Emails are the board login and already in `people.yaml`; usernames change. |
| Only approvers move items to review states, Done or Cancelled | Most boards can't restrict state changes, so triage reads the activity and reverts forbidden moves. |
| No approval gate before Todo | An approver mustn't become a bottleneck; triage's priority is marked as a default anyone can change. |
| Analysis before routing | A quick triage only translates items; bugs and features of effort `s`+ get an evidence-backed analysis first, checked by a fresh reviewer. |
| Board text is data, not instructions | Items, comments and repo files are written by people; agents never obey them. |
| An unreadable source is never "nothing to do" or green | `ERROR:` summaries count as failures; release posts `pending`, never `success`, on a check it couldn't run. |
| Every write journaled, reverts applied by a person | Blame must be trustworthy, so agents can't edit the record; undo needs judgment, so a person applies the plan. |
| Adapters for the board and code host | Supporting another tool is one file, not a rewrite. |
| Untrusted code runs without secrets | Nightly tests run in throwaway containers with only the repo mounted. |

## Known pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| Allow lists and hooks are ignored | Claude Code ignores a project's settings in an untrusted folder | Run service agents only in the trusted checkout (`/srv/agent-ops`) |
| The first tick loses an MCP server | A cold `uvx`/`npx` download beats the 30 s MCP start timeout | `MCP_TIMEOUT=120000`; warm the caches at install |
| `Bash(git -C /srv/repos/*:*)` never matches | The legacy `:*` prefix form takes no inner wildcard | Wildcard rules per git subcommand plus `additionalDirectories`; guard rule 7 |
| A read-only SELECT is blocked as DDL | Search text in a literal matched a write keyword | Blank single-quoted literals before scanning |
| People's laptops keep the old guard | Plugins update only on a new version | Bump the plugin version with every behaviour fix |
| Codex never runs the hooks | Codex runs user hooks only after they're trusted with `/hooks` | An onboarding step and a check |
| A read-only bot can't post commit statuses | Statuses need a write scope | A separate token for the release agent only |
| Agents read stale code | Clones were made from local checkouts | Fetch from the code host with the read-only token, as an HTTP header in `GIT_CONFIG_*` variables |
| Plane CE relation calls return 404 | The MCP server calls Cloud-only endpoints | `plane-mcp-ce.py` reroutes them |
| Anyone can move a task to Done | Boards can't restrict state changes | Triage reverts forbidden moves from the activity |
| Scripts get HTTP 429 from the board | Per-token rate limits | Retry with a pause in every script |
| Buffered notification lines get lost | One webhook per changed field, handled in parallel | The webhook only marks the item; a per-minute job reads the change |
| Telegram rejects some titles | Default Markdown parse mode | Send escaped HTML |
| The bot can't message someone | A bot can only message people who pressed Start | `telegram-chat-ids.sh` soon after they do |
| A script dies under `pipefail` | `tr | head` gets SIGPIPE | Generate random values with `openssl` |
| The runner fails on macOS | No `flock`, no GNU `date` | A lock directory holding the pid; portable date parsing |

## Adapting it to your team

Change (SETUP.md does most of this):
- `routing.yaml`, `projects.yaml`, `people.yaml`; the task board's projects, states, labels, bots.
- The role plugins and their skills: they encode a workflow (API contracts, E2E tests, data
  delivery). Keep what fits, rewrite the rest.
- `data-traps` and `clickhouse-query-style`: they're examples; write your own traps and style.
- Release checks (`service/release/checks/*.sql`), nightly checks (`infra/nightly/checks.yaml`),
  lineage scanners (`knowledge/lineage.yaml`).

Keep:
- The board as the one channel, the state machine, the comment tag format.
- `guard.sh` and its tests; read-only credentials first.
- The runner's gates: lock, precheck, budget, usage-limit pause, breaker, alerts, one log line per
  run.
- The unattended-run rules in every service `CLAUDE.md`.
- One bot and token per service agent; people found by email.
- The journal and the leak check.

## What to read first

1. `README.md`, then `SETUP.md`.
2. `routing.yaml` and `people.template.yaml`: the team model every agent reads.
3. `plugins/core/skills/task-protocol/SKILL.md` and `scope-guard/SKILL.md`.
4. `plugins/core/scripts/guard.sh` with `tests/guard.test.sh`.
5. `infra/bin/run-agent.sh` and `infra/bin/precheck.sh`.
6. `service/triage/` (CLAUDE.md, tick.md, settings, the triage skill).
7. `infra/adapters/README.md`, `infra/journal/README.md`.
