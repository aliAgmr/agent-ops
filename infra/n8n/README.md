# n8n: notifications and instant triage

Task-board (Plane) webhook → private Telegram messages (and optionally a Mattermost channel / DMs),
plus a nudge that starts the triage agent within seconds. Comments on tasks are written by the agents
themselves; n8n only sends the pings. Optional: without n8n everything still works, the agents just
run on their timers and nobody gets a DM.

## Run

```bash
bash infra/n8n/install.sh n8n.example.com     # pinned in VERSION; running copy in ~/n8n-selfhost
```

First visit to `http://<host>:5678` creates the owner account. Then:
1. In n8n, add a **Telegram** credential named `agent-ops-telegram` with your bot's token (and, if you
   use it, set `MATTERMOST_WEBHOOK_URL` in `~/n8n-selfhost/.env`).
2. In `~/n8n-selfhost/.env` set `PLANE_NOTIFY_TOKEN` (a copy of `TRACKER_TRIAGE_TOKEN`, read-only use)
   and `WAKE_TOKEN` (same value as in the agents' env file), then re-run `install.sh`.
3. Build and import the flow (below).
4. Point the Plane webhook (workspace settings → Webhooks) at `http://<host>:5678/webhook/plane`
   (Plane on the same host: `http://host.docker.internal:5678/webhook/plane`, and set
   `TRACKER_FLOW_URL=http://host.docker.internal`, so nothing depends on the host's network address)
   (work items + comments). Plane allows that private address because `infra/plane/install.sh` puts
   its own host into `WEBHOOK_ALLOWED_HOSTS` / `WEBHOOK_ALLOWED_IPS`.

Back up the `n8n_n8n_data` Docker volume and `~/n8n-selfhost/.env` (the encryption key) together
(`infra/macos/backup.sh` does).

## Flow `plane-notify` (built from git, not in the UI)

Source: `flows/plane-notify.js` (channel pings), `flows/dm-collect.js` + `flows/dm-flush.js` (private
messages) + the repo's config + Plane's state ids:

```bash
PLANE_API_KEY="$TRACKER_ADMIN_TOKEN" bash infra/n8n/build-flow.sh --import
```

It rebuilds `flows/plane-notify.json`, imports it, publishes it and restarts n8n. Everything team
specific comes from the repo, injected as `__CONFIG__`, `__PEOPLE__`, `__PROJECTS__`:
- **people** (names, emails, Telegram/Mattermost handles, chat ids): `people.yaml`;
- **projects** (task-board id → key): `projects.yaml` `tracker_project_id`;
- **reviewers and question owner** per project: `routing.yaml` `approvers` / `question_owner`
  (a project may override both);
- **links**: `PLANE_WEB_URL` (default `TRACKER_BASE_URL`) + `TRACKER_WORKSPACE`;
- **quiet hours and the time in messages**: `NOTIFY_TZ` (default UTC), `QUIET_FROM` / `QUIET_TO`
  (default 22 / 8; equal = none).
Re-run it after editing any of those.

**Instant triage.** The node `Wake triage` (parallel to `Route event`) POSTs every Plane event to
`http://host.docker.internal:8766/wake/triage` with header `X-Wake-Token`. That is the wake service
on the host (`infra/macos/wake.py`: launchd on macOS, the `agent-wake` systemd service on Linux),
which debounces ~10 s and starts the triage agent (or again right after a running tick ends); the
timer stays as the fallback. The node continues on error, so notifications never depend on it.

## Private messages (DMs)

Every change to a task goes to its **assignees** as a private Telegram message: state, priority,
labels, assignees added/removed, dates, title, description ("Description updated" + excerpt),
relations, parent, cycles/modules, created/deleted, and comments (new and edited). Never to the
person who made the change; changes by the agents' bot users count. Whoever was just unassigned also
hears about it. English, Telegram HTML (escaped), no link preview.

- **Reviewers and the question owner hear about some tasks that aren't theirs** (`WATCH` in
  `dm-flush.js`): state → `In Review` goes to the project's reviewers ("👀 Ready for your review"),
  labels `question` and `e2e:red` to its question owner. `Done` / `Ready for E2E` reach the assignees only.
- **Nightly findings** (label `source:nightly`, unassigned on purpose, state `Findings`) go to the
  question owner while they sit in `Findings`, including the move out of it (`PARKED`).
- **Batching:** one message per person covering all their changed tasks (task key + title + link,
  then one line per change with who and when). It goes out once that person's oldest unsent change
  is 5 minutes old; longer batches are split below 3900 characters (the task header repeats).
- **Quiet hours:** nothing is sent; the first run after them sends what piled up as one
  "Overnight digest".
- **Nodes:** `Plane webhook` → `Collect DMs` (`flows/dm-collect.js`) only marks the task as changed.
  `Every minute` (Schedule) → `Flush DMs` (`flows/dm-flush.js`) reads each marked task (plus any task
  updated in the last 15 min) from Plane's API: details, activity log and comments, with
  `PLANE_NOTIFY_TOKEN`. It emits one item per message → `Telegram DM?` → `Send DM (Telegram)` (chat id
  from people.yaml) and → `Mattermost DMs on?` → `Send DM (Mattermost)`. Both senders continue on error.
- **Why from Plane's activity and not the webhook payloads:** Plane sends one webhook per changed
  field, all within a few ms, and n8n runs them in parallel; each execution loads the workflow static
  data at its start and writes all of it back at its end, so buffered lines would overwrite each
  other. Markers for the same task are identical, so nothing is lost; the 15-minute re-scan covers a
  marker lost when two different tasks change in the same instant.
- **State** lives in the workflow static data (`dm`: markers, remembered titles/assignees for
  deleted tasks, per-person "sent up to" cursors). n8n saves static data only for production runs
  of the published flow, and re-importing the flow may reset it: changes still inside their 5-minute
  window can then be lost, and changes of the last 15 minutes can be sent twice.
- **Chat ids:** a bot can only DM someone who opened it and pressed Start. Ask people to do that,
  then (within ~24 h, Telegram's getUpdates limit) run `bash infra/n8n/telegram-chat-ids.sh` to list
  the private chats, or `--write` to fill `telegram_chat_id` in people.yaml by `telegram_username`.
  The bot token stays inside the n8n container. Rebuild + import afterwards.
- **Mattermost DMs** are built but off: set `MM_DM_ENABLED=true` in `~/n8n-selfhost/.env` and re-run
  `install.sh`. They post `{"channel": "@<mattermost_username>"}` to `MATTERMOST_WEBHOOK_URL`, which
  only works if that incoming webhook is not locked to its channel.
- **Tests:** `bash tests/n8n-dm.test.sh` (node ≥ 18; fake Plane, static data and clock).

## Channel pings (`plane-notify.js`, Mattermost)

- work item assigned → the new assignees
- label `question` → the question owner; `e2e:red` → the assignees + the question owner
- state → `In Review` → the reviewers; `Ready for E2E` / `Done` → a channel post
