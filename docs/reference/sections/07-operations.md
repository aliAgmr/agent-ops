# Operations

## Deploy

CI (GitHub Actions, `.github/workflows/ci.yml`) runs on every pull request and on `main`: JSON and
shell syntax, executable bits, skill front matter, `claude plugin validate`, every test, and the
leak check. A merge to `main` reaches the host by itself: `run-agent.sh` pulls `/srv/agent-ops`
before every tick. Things that need a step after a merge:

| What | After a merge |
|---|---|
| Service-agent prompts, skills, settings, scripts | nothing: the next tick pulls them |
| agent-mcp (`infra/agent-mcp/`, journal, lineage) | restart the agent-mcp service |
| launchd/systemd units | re-run `infra/macos/install.sh` or `infra/bootstrap.sh` |
| n8n flows | `infra/n8n/build-flow.sh --import` |
| People's plugins | bump the plugin version; people update (section 02) |
| Cursor/Codex/Kimi installs | they pull within an hour at session start |

## Pausing and resuming an agent

- Pause one: create `<log dir>/paused` with `until=<epoch>` and `reason=…`, or stop its timer
  (`launchctl bootout …` / `systemctl stop agent-<name>.timer`).
- Resume: `rm <log dir>/paused <log dir>/fail-count`.
- Run one now: `launchctl kickstart -k gui/$(id -u)/<label>` or `systemctl start agent@<name>`.

## Reading logs

```bash
L=~/Library/Logs/agent-ops        # Linux: /var/log/agents
tail -1 $L/triage/$(date +%F).jsonl | jq '{outcome, model, cost: .result.total_cost_usd, summary: (.result.result | split("\n")[0])}'
tail $L/triage/skipped.log         # why ticks didn't run (paused, budget, already running)
tail $L/triage/last.err $L/triage/precheck.err
tail $L/alerts.log
```

## Alerts

`alert.sh` sends a private Telegram message to the question owner and always logs it. You get one
when an agent pauses after repeated failures (and when it recovers), when the plan's usage limit is
hit, when the budget is reached, and when the nightly pass fails.

## Budget and cost controls

- `AGENT_BUDGET_5H_USD`: the 5-hour API-price spend, summed from the run logs, above which ticks are
  skipped. Size it to stop a runaway, not normal days.
- `AGENT_MODEL_<AGENT>`, `AGENT_MAX_TURNS_<AGENT>` per agent.
- The precheck skips idle ticks; the analyst does one item per run.

## Common failures and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| Every tick fails at once | Claude auth expired or the plan limit | `claude setup-token` again or set `ANTHROPIC_API_KEY`; usage limits pause by themselves |
| An agent's tool calls are all denied | It runs outside the trusted folder | Run from `/srv/agent-ops`; trust it once interactively |
| The task-board MCP server fails to start | Cold package cache | Run the agent once by hand; `MCP_TIMEOUT` is 120 s |
| Triage never wakes instantly | Webhook or wake listener down | Check n8n's executions and the wake listener's `/health` |
| Release posts `pending` forever | A check is unreadable or the E2E job is missing | Read the merge request comment: it names the check |
| agent-mcp says 401 | The person's task-board token is wrong or expired | They update the core plugin setting |

## Blame and revert

```bash
infra/bin/journal blame BIL-123                     # who changed it: person, their agent, a service-agent run, by hand
infra/bin/journal suspects BIL analytics.orders --days 14   # recent commits and agent changes that touch it
infra/bin/journal show --run triage-20250101T101500-4242
infra/bin/journal revert-plan --run triage-20250101T101500-4242   # dry run → plan rv-…
infra/bin/journal revert-apply rv-1a2b3c4d           # a person, in a terminal: type the plan id
```

A teammate can ask their agent to "revert what triage did to BIL-40"; the agent returns a plan id
and an approver applies it on the host. `revert-apply` refuses a `revert.env` others can read.

## Lineage and trap checks

```bash
infra/bin/lineage impact analytics.payments_ledger   # what changes if it changes
infra/bin/lineage upstream /dashboard                # what a page is built from
infra/bin/lineage files src/repositories/revenue.repository.ts --impact
infra/bin/lineage build                              # refresh the cache now
infra/bin/lineage check                              # hand-file names that no longer exist
infra/bin/traps run                                  # every data-trap check (read-only)
infra/bin/traps for-files <changed paths>            # the checks a change reaches
```

A new check's `expect` is today's healthy value; use a baseline only when the current value isn't
zero yet, and say so in the check.

## Jenkins watcher

```bash
python3 infra/telegram/poll.py chats                 # group/topic ids and the bots seen there (setup)
python3 infra/telegram/poll.py recent 5              # the last messages kept from the watched topic
python3 infra/jenkins-watch/watch.py status          # open incidents (failed / stuck / queue)
bash infra/bin/run-agent.sh jenkins-watch            # one tick now (collect runs first)
```

- Pause it like any agent (`<log root>/jenkins-watch/paused`), or turn its schedule off
  (`launchctl bootout gui/$(id -u)/io.agent-ops.agent-jenkins-watch`;
  `systemctl disable --now agent-jenkins-watch.timer`). The poller is independent.
- A job is mapped to the wrong project or owner: edit `knowledge/jenkins-owners.yaml` (first match
  wins).
- It never replies to the CI bot's alerts: check that Bot-to-Bot Communication Mode is on and that
  the bot is still an admin (`poll.py chats` lists the notifier under `bot_senders`).

## Tests

Every test is offline: each fakes the external tool it needs (curl, claude, launchctl, git, the
task board, the code host) as a script first on `PATH` or a local HTTP server.

```bash
for t in tests/*.test.sh tests/agent-mcp/readonly-hook.test.sh tests/agent-mcp/run-offline.sh; do bash "$t" || echo "FAILED: $t"; done
for t in tests/*.test.py; do python3 "$t" || echo "FAILED: $t"; done
uv run --project clients python tests/clients.test.py
python3 tools/leak-check.py
```

Each prints `ok` or `FAIL` per check and exits non-zero on any failure.

## Adding a teammate

1. Add them to `people.yaml` and `routing.yaml` (or re-run SETUP.md).
2. Add them to the task board's projects (`infra/plane/configure.sh` for Plane).
3. Generate their onboarding prompt (SETUP.md's last step) and send it to them.
4. After they press Start on the Telegram bot: `infra/n8n/telegram-chat-ids.sh --write`, then
   rebuild the n8n flow.

## Adding a project

1. A board project with the same states and labels (`configure.sh`).
2. `projects.yaml` (its `tracker_project_id`, repos, data sources) and `routing.yaml` (`projects:`
   overrides).
3. Clone its repos on the host (`repo-sync.sh`), and add their code-host tokens if it's a new host.

## Adding a service agent

1. `service/<name>/` with `CLAUDE.md`, `tick.md`, `.claude/settings.json` (copy the guard and
   journal hooks), `.mcp.json`, skills.
2. Its case in `run-agent.sh` (model, turns) and `precheck.sh`.
3. A launchd template entry and a systemd timer (jenkins-watch: a flag that adds it to the
   installers' `AGENTS` list).
4. Tests in `tests/run-agent.test.sh` and `tests/precheck.test.sh`; a section here.
