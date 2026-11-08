# Jenkins watcher (optional)

Every 15 minutes it looks at all Jenkins jobs. When something changed, it tells the team's Telegram
topic in a few English lines which jobs need a person and which are passing blips, and mentions the
job's owner. It also replies to a Jenkins notifier bot's own alerts in that topic with its
assessment. It is read-only on Jenkins and never runs a job.

```
every 60 s       infra/telegram/poll.py      getUpdates → the watched topic's messages (SQLite)
every 15 min     run-agent.sh jenkins-watch
                   precheck: watch.py collect   Jenkins MCP (read-only) + new alerts → facts.json
                             exit 1 = nothing new → no Claude run; 3 = Jenkins unreadable
                   service/jenkins-watch (Sonnet, 40 turns): reads logs, decides → decisions.json
                   watch.py post                 one summary + replies → Telegram; ids → state.json
```

It needs a Jenkins MCP server (the Jenkins MCP Server plugin; `JENKINS_MCP_URL`, `JENKINS_RO_AUTH`
for a user with Overall/Read + Job/Read and no Build) and a Telegram group with forum topics that
the team bot (`TELEGRAM_BOT_TOKEN`) can post in.

## What counts

| Incident | When | Event |
|---|---|---|
| failed | the last finished build is red (failure), yellow (unstable) or aborted | `new`, `again` (a newer build failed too) |
| stuck | a build runs longer than `JENKINS_WATCH_STUCK_FACTOR` (2) × its usual duration, at least `JENKINS_WATCH_STUCK_MIN` (30) min | `new` |
| queue | builds waiting to start in two checks in a row | `new` |
| — | an incident the group heard about turns green | `recovered` (reply to the earlier message) |
| — | another bot posts in the topic | `alerts` (linked to the jobs its text names) |

The **usual duration** is the median of the last 10 finished builds the watcher saw. Before it has
seen one, it uses the previous build's duration. Disabled and never-built jobs and folders are
skipped.

The agent's **verdicts** are `attention` (owner mentioned), `stuck` (owner mentioned), `transient`
(a passing blip, such as a dropped connection the next build fixed) and `recovered`. An unchanged
incident is never reposted, and a failure that recovers before anyone was told closes quietly.

**Project and owner** come from `knowledge/jenkins-owners.yaml`: ordered job-name patterns → a
project key (or `infra` / `other`) and a `people.yaml` key. A mention is the person's
`telegram_username`, or a link to their `telegram_chat_id` when they have no username.

## Setup (once)

1. In @BotFather, turn on **Bot-to-Bot Communication Mode** for the team bot. Without it, Telegram
   never delivers another bot's messages to it.
2. Add the team bot to the group as an **admin**, allowed to post in the Jenkins topic.
3. Install the poller and the agent with `JENKINS_WATCH=true` (macOS: `bash infra/macos/install.sh`;
   Linux: `sudo bash infra/bootstrap.sh`). The poller starts at once; the agent's schedule follows
   `ENABLE_TIMERS` like the other agents.
4. Send any message in the topic, wait a minute, then run `python3 infra/telegram/poll.py chats`. Put
   the group's `chat_id` and the topic's `thread_id` in the env file as `TELEGRAM_WATCH_CHAT_ID` /
   `TELEGRAM_WATCH_TOPIC_ID`, and the bot's own username as `TELEGRAM_BOT_USERNAME`.
5. Write `knowledge/jenkins-owners.yaml` for your jobs, then run one tick by hand:
   `bash infra/bin/run-agent.sh jenkins-watch`. Its precheck (`watch.py collect`) runs on every tick,
   even the forced full run after `AGENT_MAX_IDLE_HOURS`, since it writes the facts the agent reads.
   The first collect of a busy Jenkins reports every job already red as `new`.

| | macOS (launchd) | Linux (systemd) |
|---|---|---|
| agent | `io.agent-ops.agent-jenkins-watch`, every `JENKINS_WATCH_INTERVAL` (900) s | `agent-jenkins-watch.timer` → `agent@.service` (instance `jenkins-watch`) |
| poller | `io.agent-ops.telegram-poll`, every 60 s | `telegram-poll.timer` → `telegram-poll.service` |

The poller consumes the bot's updates, which `infra/n8n/telegram-chat-ids.sh` also reads: stop the
poller while people press Start on the bot and you run that script.

## Files

- `~/.local/share/agent-ops/telegram/messages.sqlite3` (mode 600): the watched chat's messages, kept
  30 days. No other chat's text is stored; `chats` keeps only ids, titles and bot usernames of groups.
- `~/.local/share/agent-ops/jenkins-watch/state.json`: open incidents, the message ids sent, build
  durations. `facts.json` holds the last collect; `decisions.json` is what the agent decided, renamed
  to `decisions.posted.json` once sent.
- `watch.py status` shows the open incidents; `poll.py recent 5` the last stored messages.

The agent writes only `decisions.json`. `watch.py post` alone talks to Telegram: one summary per run,
at most 5 replies per job one second apart, and each report recorded in the action journal
(irreversible: a sent message can't be unsent). Tests: `python3 tests/jenkins-watch.test.py`.
