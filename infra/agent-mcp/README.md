# agent-mcp: ask the project agent

A remote MCP server on the agent host that teammates' own agents (Claude Code through the `core`
plugin, Cursor, Codex and Kimi through the `agent-ops` installer: server `agent-mcp`) call to ask
about the whole project: code in every repo, the application database and warehouse, CI, the code
host and the task board, which they may not have access to themselves. A read-only Claude Code
agent (`service/agent-mcp`) investigates and answers in English, for the calling agent, with
sources. It explains and guides; it never builds or changes anything.

It also sends **messages between teammates**: a person's agent can send a private Telegram message
from the team bot to other people or a whole team, signed with that person's name
(`list_teammates`, `send_message`; core skill `message-teammate`). One-way: the message tells the
recipient to reply to the sender directly.

And it keeps the team's **action journal** (`POST /journal`): see `infra/journal/README.md`.

```
teammate's agent ──HTTPS──▶ your reverse proxy (e.g. https://agents.example.com/agent-mcp/*)
   core plugin                  └─▶ 127.0.0.1:8765 server.py (FastMCP, streamable HTTP)
   Authorization: Bearer             ├─ checks the token with the task board through the tracker
   <task-board token>                │  adapter (infra/adapters: whoami, projects), 5 min cache
                                     ├─ SQLite ~/.local/share/agent-mcp (conversations, tickets, journal)
                                     └─ claude -p in /srv/agent-ops/service/agent-mcp
                                        (--session-id first, --resume after; read-only profile)
```

Public endpoint: `AGENT_MCP_PUBLIC_URL` + `/mcp` (for example
`https://agents.example.com/agent-mcp/mcp`); on the host `http://127.0.0.1:8765/mcp`. SETUP.md
asks for the public URL and writes it into every client's config.

## Tools

| tool | what |
|---|---|
| `list_projects()` | projects from `projects.yaml` whose task-board project the caller is a member of |
| `create_conversation(project, title)` | new private conversation → `conversation_id` |
| `list_conversations(project?, include_closed?)` | the caller's own conversations |
| `rename_conversation(id, title)`, `close_conversation(id)` | |
| `ask(conversation_id, question, depth?)` | queues a run, returns a `ticket` at once; `depth` quick / default / deep |
| `get_answer(ticket, wait_seconds≤50)` | long-polls; `{status: queued/running/done/error, answer, sources}` |
| `list_teammates(project?)` | people in people.yaml: key, name, role, teams in your projects (`BIL:backend`), Telegram reachable |
| `send_message(to, text, project?, channel?)` | private Telegram message to people (`["Noor"]`) or a team (`["team:backend"]` + `project`) → `{sent, failed}` |
| `blame(target, since_days?)` | who changed a task / MR / PR / target, how (by hand, a person's agent, a service-agent run), when, how to undo it (action journal) |
| `suspects(project, term, since_days?)` | recent commits in the project's repos and journal events that touch a table / file / route / page, and (through lineage) what it is built from |
| `lineage(node, direction?)` | lineage (`infra/lineage`): `impact` = views, routes, pages and metrics downstream of a table / route / page / file; `upstream` = what it is built from; with owners |
| `revert_plan(event?, run?, session?, target?, actor?, since_hours?)` | dry-run plan that undoes those changes; a person applies it on the host (`infra/bin/journal revert-apply`) |

Plus `POST /journal` and `/journal/batch` (not MCP): the core plugin's `journal.sh` hook reports
each write tool call there, with the caller's token. See `infra/journal/README.md`.

## Rules it enforces

- **Auth:** the caller's task-board token (`Authorization: Bearer …` or `X-API-Key`), checked
  through the tracker adapter (`routing.yaml` → `tracker`, Plane built in): identity = `whoami()`;
  project access = `projects()` with `is_member` true, matched against each project's
  `tracker_project_id`. No token / bad token → HTTP 401. Cached 5 minutes.
- **Privacy:** conversations and tickets belong to the user who created them; others get
  "not found".
- **One run per conversation at a time** (FIFO queue); different conversations run concurrently,
  no cap.
- **Model:** `AGENT_MCP_MODEL` (an Opus model by default); depth quick = effort low / 20 turns,
  default = medium / 50, deep = high / 120. Same Claude auth as the service agents
  (`~/.config/agent-ops/env`, `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`; an empty value is
  dropped). `MCP_TIMEOUT=120000`.
- **Secrets never leave:** read-only credentials, `plugins/core/scripts/guard.sh`, the profile's
  `readonly.sh` hook (task-board read actions only, no reads of secret paths), its instructions,
  and a hard output filter replacing every value ≥ 8 chars from the env file and
  `~/.config/agent-ops/*.token` (and the user/password parts of URLs and Basic auth values in
  them, and the caller's own token) with `[redacted]`.
- **No content logging:** logs hold ticket/conversation ids, depth, status, exit code, turns and
  seconds; never questions or answers. Answers are kept in the SQLite database (mode 600) for
  `get_answer`, deleted after 14 days. Questions are not stored. Each answer adds one line of claim
  counts (no text) to `answers-quality.jsonl` (`infra/quality/claims.py`).
- **Messages** (`send_message`):
  - Sender = the caller's task-board email matched to `people.yaml` (case-insensitive); nobody
    else can send, and nobody can send as someone else. Header: `💬 From <full name> (via Claude)`,
    footer: reply to them directly (their `telegram_username`).
  - Recipients: names, keys, first names, Telegram usernames or emails from people.yaml; teams
    from routing.yaml (`teams` for the default project — `default_project`, else the first project
    in projects.yaml — and `projects.<KEY>.teams` for the others), only in projects the caller is
    a member of. The sender is left out of team messages. Ambiguous or unknown names are refused.
    If anyone has no `telegram_chat_id`, nothing is sent.
  - Telegram only (`TELEGRAM_BOT_TOKEN` from the env file, read per send). Other channels are
    refused until an adapter for them exists.
  - Limits: 3500 characters, `AGENT_MCP_MSG_PER_HOUR` (20) per sender, each recipient counts.
  - The text passes the secret filter and is HTML-escaped. The `messages` table and the log keep
    sender, recipient, channel, status and time only, never the text; rows are deleted after 90 days.
  - people.yaml and routing.yaml are re-read when they change (no restart).

## Files

- `server.py` — the server (PEP 723 inline deps: `mcp==1.30.0`, `pyyaml`; run with `uv run --script`).
  It imports `infra/journal`, `infra/lineage`, `infra/quality` and, through `journal.Connector`,
  the adapters in `infra/adapters`.
- `agent-mcp.plist.template` — launchd job `<LABEL_PREFIX>.agent-mcp` (KeepAlive), `__HOME__` /
  `__PREFIX__` placeholders filled by `infra/macos/install.sh`. On Linux the same is the systemd unit in `infra/systemd/`.
- `../../projects.yaml` — project key → task-board project id (`tracker_project_id`), repos, data sources.
- `../../people.yaml`, `../../routing.yaml` — who can send and receive messages, and the teams.
- `../../service/agent-mcp/` — the answering agent's profile (CLAUDE.md, settings, .mcp.json, hook).
- `../../tests/agent-mcp/` — offline tests (`run-offline.sh`), hook tests, live test (`test_live.py`).

## Install / update

SETUP.md installs it (`AGENT_MCP=true bash infra/macos/install.sh` on macOS, which renders
the template with your `LABEL_PREFIX`, default `io.agent-ops`). Check it:

```bash
curl -s http://127.0.0.1:8765/healthz
```

On Linux: `systemctl --user enable --now agent-mcp` (or the system unit from `infra/systemd/`).

The server runs Claude in `/srv/agent-ops/service/agent-mcp`, which must be a trusted folder
(`/srv/agent-ops` is, once you have opened it in Claude Code and accepted the trust prompt): in
an untrusted folder Claude ignores the allow list and the hooks. Put any HTTPS reverse proxy in
front of `127.0.0.1:8765` for teammates (the Caddy in `infra/plane/subpath/` is one example).

Environment (all optional): `AGENT_MCP_HOST` (127.0.0.1), `AGENT_MCP_PORT` (8765),
`AGENT_MCP_PROFILE_DIR`, `AGENT_MCP_PROJECTS`, `AGENT_MCP_DATA_DIR`, `AGENT_OPS_ENV`,
`AGENT_MCP_MODEL`, `AGENT_MCP_CLAUDE`, `AGENT_MCP_PEOPLE`, `AGENT_MCP_ROUTING`,
`AGENT_MCP_TELEGRAM_API` (https://api.telegram.org), `AGENT_MCP_MSG_PER_HOUR` (20),
`AGENT_MCP_TRACKER_URL` / `AGENT_MCP_TRACKER_WORKSPACE` (default `TRACKER_BASE_URL` /
`TRACKER_WORKSPACE` from the env file), `AGENT_MCP_AUTH_TTL` (300), `AGENT_MCP_RUN_TIMEOUT`
(1800 s), `AGENT_MCP_TICKET_TTL_DAYS` (14). Read-only code-host tokens for journal snapshots:
`CODEHOST_<KEY>_TOKEN`.

Logs: `~/Library/Logs/agent-ops/agent-mcp/launchd.err` (macOS), `journalctl --user -u agent-mcp` (Linux).

## Tests

```bash
bash tests/agent-mcp/run-offline.sh           # fake tracker adapter + fake claude, real server and MCP client
bash tests/agent-mcp/readonly-hook.test.sh    # the profile's read-only hook
AGENT_MCP_TEST_SYMBOL=<a function in your code> uv run --script tests/agent-mcp/test_live.py   # running server, real Claude
```

## Adding a project

Add it to `projects.yaml` (task-board project id, repos cloned under `/srv/repos`, data sources),
make sure the answering agent's `.mcp.json` credentials can read its data, and restart the job
(`launchctl kickstart -k gui/$(id -u)/io.agent-ops.agent-mcp`, or `systemctl --user restart agent-mcp`).
