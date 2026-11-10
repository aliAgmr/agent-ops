# Agent host: macOS and Linux

The service agents (triage, analyst, release, nightly) and the always-on services (wake, agent-mcp,
tunnels, backups) run on one **agent host**. It can be a Mac (this folder, launchd) or a Linux
server or VM (`infra/bootstrap.sh` + `infra/systemd/`). Everything else in the repo is the same on
both: a Mac gets Linux's `/srv` paths through a synthetic link, so prompts, permission rules and
`run-agent.sh` don't change. `SETUP.md` walks through either path.

| | Linux (`infra/bootstrap.sh`) | macOS (`infra/macos/install.sh`) |
|---|---|---|
| Code and clones | `/srv/agent-ops`, `/srv/repos` | same paths; `/srv` → `~/agent-host` (`srv-link.sh`) |
| Env file | `/etc/agent-ops/env` (root:agents, 640) | `~/.config/agent-ops/env` (chmod 600) |
| Interval agents | `agent@.service` + `agent-<name>.timer` | `~/Library/LaunchAgents/io.agent-ops.agent-<name>.plist` |
| Nightly | `nightly.timer`, `nightly-send.timer` | `io.agent-ops.nightly`, `io.agent-ops.nightly-send` |
| Logs | `/var/log/agents/<agent>/`, `journalctl -u <unit>` | `~/Library/Logs/agent-ops/<agent>/` |
| Runs as | user `agents` | the logged-in user (Docker Desktop and launchd user agents need a session) |

The launchd label prefix is `io.agent-ops` (change it with `LABEL_PREFIX`; `wake.py` reads the same
value). Both installers are idempotent: re-run them after changing a setting or pulling a new version.

## Set up (once)

macOS:

```bash
sudo pmset -a sleep 0 disksleep 0 autorestart 1 womp 1    # always on, restart after a power cut
sudo bash infra/macos/srv-link.sh "$HOME/agent-host"     # /srv, no reboot
bash infra/macos/install.sh                              # clones, env file, launchd agents (off)
```

Linux:

```bash
sudo bash infra/bootstrap.sh                             # packages, user, env file, clones, units (off)
```

Then fill the empty values the installer lists in the env file (Claude token, task board and code
host tokens, read-only data sources, …): `SETUP.md` asks for each and you type them yourself. Re-run
the installer so it can clone the repos with the new tokens, and test one tick by hand before
turning the schedules on:

```bash
# macOS
AGENT_OPS_ENV=~/.config/agent-ops/env bash /srv/agent-ops/infra/bin/run-agent.sh triage
ENABLE_TIMERS=true bash infra/macos/install.sh
# Linux
sudo systemctl start agent@triage && journalctl -u agent@triage -e
sudo ENABLE_TIMERS=true bash infra/bootstrap.sh
```

By default triage runs every 5 minutes, the analyst every 10 and release every 15
(`AGENTS="triage:300 analyst:600 release:900"`; drop an entry to skip an agent).

Status and logs on macOS: `launchctl print gui/$(id -u)/io.agent-ops.agent-triage`,
`~/Library/Logs/agent-ops/triage/<date>.jsonl`; turn one off with
`launchctl bootout gui/$(id -u)/io.agent-ops.agent-triage`. On Linux: `systemctl list-timers 'agent-*'`,
`systemctl disable --now agent-triage.timer`.

## Settings

Environment variables for either installer (all optional):

| Setting | macOS | Linux | What it does |
|---|---|---|---|
| `AGENTS` | ✓ | ✓ | interval agents and their period in seconds |
| `ENABLE_TIMERS=true` | ✓ | ✓ | turn the agents' schedules on (off until a manual run passed) |
| `NIGHTLY=true` | `NIGHTLY_AT=02:30` `NIGHTLY_DAYS=1,2,3,4,5` `NIGHTLY_SEND_AT=08:30` `NIGHTLY_SEND_DAYS=1,2,3,4,5` (0 = Sunday) | `NIGHTLY_ONCALENDAR="Mon..Fri 02:30"` `NIGHTLY_SEND_ONCALENDAR="Mon..Fri 08:30"` | the nightly pass and its morning messages, host's local time (`infra/nightly/README.md`) |
| `WAKE=true` | ✓ | ✓ | instant triage: n8n calls the wake service on :8766 on every task-board change; triage starts within ~10 s (bursts collapse into one run). The interval timer stays as a safety net. |
| `AGENT_MCP=true` | ✓ | ✓ (`AGENT_MCP_HOST`) | the project Q&A + action journal server on :8765 (`infra/agent-mcp/README.md`) |
| `JENKINS_WATCH=true` | `io.agent-ops.agent-jenkins-watch`, `io.agent-ops.telegram-poll` | `agent-jenkins-watch.timer`, `telegram-poll.timer` | the optional Jenkins watcher (`JENKINS_WATCH_INTERVAL`, default 1800 s; follows `ENABLE_TIMERS`) and its Telegram poller (every 60 s, started at once); `infra/jenkins-watch/README.md` |
| `BACKUP=true` | `BACKUP_AT=03:15` | `BACKUP_ONCALENDAR="*-*-* 03:15"` | nightly backup, below; `OFFSITE_DIR` for an off-host copy |
| `TUNNEL=true` | ✓ | ✓ | SSH local forwards to the read-only data sources, below |
| `PUBLISH_TUNNEL=true`, `BI_TUNNEL=true` | ✓ | ✓ | SSH remote forwards that publish the task board (and its database for BI) on a gateway, below |
| `TRACKER_BASE_URL`, `TRACKER_WORKSPACE` | ✓ | ✓ | written into the env file |
| `LABEL_PREFIX` | ✓ | | launchd label prefix (`io.agent-ops`) |
| `CLAUDE_CODE_VERSION` | | ✓ | Claude Code version for the `agents` user (`stable`) |

Both installers also render the service agents' MCP configs (`infra/bin/render-mcp.py all`; they
are git-ignored and re-rendered before every tick).

### Cost and safety gates (run-agent.sh)

- **Model:** triage and release run on Sonnet (turn caps 80 and 40), the analyst on Opus (150), the
  nightly agent on Sonnet (300). Override with `AGENT_MODEL_<AGENT>` / `AGENT_MAX_TURNS_<AGENT>`
  (`TRIAGE`, `ANALYST`, `RELEASE`, `NIGHTLY`, `JENKINS_WATCH`: Sonnet, 60) in the env file.
- **Budget:** a tick is skipped when all service agents together spent `AGENT_BUDGET_5H_USD`
  (default $60, API prices, from the `.jsonl` logs) in the last 5 hours. One alert per 5 hours.
- **Usage limit:** when Claude reports the plan's usage limit, the agent pauses until the reset time
  (or an hour), with one alert.
- **Circuit breaker:** after `AGENT_BREAKER_FAILS` (3) failed runs in a row (error exit, error
  result, or a summary that starts with `ERROR:`) the agent pauses for an hour, then probes once an
  hour. Alerts on pause and on recovery go privately to the question owner. Resume by hand: delete
  `paused` and `fail-count` in the agent's log folder.
- Why a tick was skipped: `<log folder>/<agent>/skipped.log`; alerts: `<log folder>/alerts.log`.

## Code and read-only repo clones

`/srv/agent-ops` is a clone of your agent-ops repo on its default branch: each tick pulls it
(`git pull --ff-only`), so a merge reaches the agents within a tick. Long-running services (wake,
agent-mcp) need a restart to pick up their own code.

The triage, agent-mcp, nightly and jenkins-watch agents read code from `/srv/repos/<repo>`: one clone per repo in
`routing.yaml`, made by the installer. `run-agent.sh` pulls them before each triage and nightly
tick through `infra/bin/repo-sync.sh`, with the read-only token of the repo's code host
(`CODEHOST_<KEY>_TOKEN`, GitLab or GitHub). The token reaches git only through `GIT_CONFIG_*`
variables (an `http.<url>.extraHeader`), never on a command line or in a git config file, and
credential helpers are off for these fetches. A failed pull is logged to the agent's error log and
the tick runs on the last copy.

Large repos of which the agents need only a part can be fetched blobless (`AGENT_BLOBLESS_REPOS`,
folder names): the checked-out files are complete, but `git show` / `git log -p` on an older
version of a file can need a fetch the agents can't make.

## SSH tunnels

For hosts that can't reach the data sources directly, or that others can't reach:

| Flag | macOS job / Linux unit | Script | What it does |
|---|---|---|---|
| `TUNNEL=true` | `io.agent-ops.agent-tunnel` / `agent-tunnel.service` | `tunnel.sh` | `-L` forwards from `TUNNEL_FORWARDS` (`local_port:remote_host:remote_port …`), e.g. ClickHouse, Postgres, the Jenkins MCP |
| `PUBLISH_TUNNEL=true` | `io.agent-ops.publish-tunnel` / `agent-publish-tunnel.service` | `publish-tunnel.sh` | `-R` publishes the task board on the gateway (`PUBLISH_BIND:PUBLISH_PORT` → `localhost:PUBLISH_LOCAL_PORT`), e.g. as a reverse proxy's backend |
| `BI_TUNNEL=true` | `io.agent-ops.bi-tunnel` / `agent-bi-tunnel.service` | `publish-tunnel.sh` | `-R` publishes the task board's database for BI (`BI_BIND:BI_REMOTE_PORT` → `127.0.0.1:BI_LOCAL_PORT`; `infra/plane/bi/README.md`) |

All take `TUNNEL_TARGET=user@gateway` (required), `TUNNEL_TARGET_PORT` (22), `TUNNEL_JUMP`
(optional `user@host[:port]`) and `TUNNEL_KEY`. On macOS a key with a passphrase needs it in the
Keychain first (`ssh-add --apple-use-keychain <key>`); on Linux use a dedicated key without a
passphrase, owned by `agents` (`/home/agents/.ssh/id_ed25519`), since systemd has nothing to unlock
it with. Run each script once by hand to accept the host keys. Remote forwards on `0.0.0.0` need
`GatewayPorts clientspecified` in the gateway's sshd.

```bash
TUNNEL=true TUNNEL_TARGET=agent@192.0.2.20 \
  TUNNEL_FORWARDS="18123:ch.example.com:8123 15431:db.example.com:5432" bash infra/macos/install.sh
```

Each log line starts with its UTC time; `start` and `ssh exited <code>` mark every reconnect. If the
gateway still holds a remote port from a dropped session, the script exits and the supervisor
retries every 5 s until the port is free; a short `ClientAliveInterval` in the gateway's sshd
shortens that.

## Backups

`BACKUP=true` runs `backup.sh` nightly into `$BACKUP_DIR/<date>/` (macOS `~/agent-backups`, Linux
`/srv/backups`; owner-only, `KEEP_DAYS` 14): the task board's Postgres dump (checked with
`pg_restore --list`), its uploads and `plane.env`; n8n's data volume, workflow and credential
exports and its `.env`; the agents' config folder. A part that isn't running is skipped. Set
`OFFSITE_DIR=<mounted share>` so each night is also copied off the host; without it a disk failure
loses both. Where the task board and n8n were installed: `PLANE_DIR`, `N8N_DIR` (defaults
`~/plane-selfhost/plane-app`, `~/n8n-selfhost`); their compose project names: `PLANE_PROJECT`
(`plane-app`), `N8N_PROJECT` (`n8n`).

Run now: `launchctl kickstart gui/$(id -u)/io.agent-ops.agent-backup` (logs in
`~/Library/Logs/agent-ops/backup/`) or `sudo systemctl start agent-backup.service`.

Restore the task board (stop it first: `cd ~/plane-selfhost/plane-app && docker compose stop api worker beat-worker`):

```bash
D=~/agent-backups/<date>
docker exec plane-app-plane-db-1 sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" dropdb -h localhost -U "$POSTGRES_USER" "$POSTGRES_DB" && PGPASSWORD="$POSTGRES_PASSWORD" createdb -h localhost -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker exec -i plane-app-plane-db-1 sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -h localhost -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' < "$D/plane-db.dump"
docker run --rm -v plane-app_uploads:/v -v "$D":/b postgres:15.7-alpine sh -c 'rm -rf /v/* && tar xzf /b/plane-uploads.tgz -C /v'
cd ~/plane-selfhost/plane-app && docker compose up -d
```

Restore n8n: stop it (`docker compose -p n8n stop`), put `n8n.env` back as `~/n8n-selfhost/.env`,
untar `n8n-data.tgz` into the `n8n_n8n_data` volume the same way, start it. The `.env` must match
the data, or the saved credentials can't be decrypted. Test a restore into a scratch container once,
before you need it.

## Health

```bash
curl -s 127.0.0.1:8766/health      # wake
curl -s 127.0.0.1:8765/healthz     # agent-mcp
```

## Other scripts here

- `set-env-from-clipboard.sh KEY`: saves a token from the clipboard into the env file without
  showing it, checking task-board and code-host tokens first (macOS `pbpaste`; Linux `wl-paste` or
  `xclip`).
- `srv-link.sh`: the `/srv` synthetic link on macOS.
- `wake.py`: the wake service for both supervisors (`WAKE_SUPERVISOR=launchd|systemd`).
