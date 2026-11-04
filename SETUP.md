# SETUP: set up agent-ops in one go

**For the AI agent reading this** (Claude Code, Codex, Kimi or Cursor): follow this file from top to
bottom to set up agent-ops for this team. You do the work; the person answers questions and types
secrets.

## Rules for the agent

1. **Ask everything first.** Section 1 is one round of questions, sent together, each with a
   sensible default. Don't install or change anything until the person has answered and confirmed
   the plan (section 2).
2. **Never handle secrets.** Never ask for a token, password or key in the chat, never print one,
   and never put one on a command line. For each secret, give the person this command to run in
   their own terminal (hidden input):
   `bash tools/set-secret.sh <env file> <NAME>`
   Then check it worked by calling the service and printing only who the token belongs to.
3. **One step at a time, checked.** After each step, run its check and say in one line what is now
   true. If a check fails, stop, explain, and fix it with the person before going on.
4. **Safe to re-run.** Every script here is idempotent. If this setup was interrupted, read
   `setup.answers.yaml` (git-ignored) and continue from the first unfinished step.
5. **Nothing leaves this machine without asking.** Pushes, invitations and messages to people need a
   yes for each one.

## 1. Questions (ask them all at once)

Save the answers to `setup.answers.yaml` in the repo root (git-ignored) as you go.

**A. Team.** For each person:
- a short key (e.g. `alex`) and full name;
- role: `product`, `design`, `backend`, `frontend`, `data` or `devops`;
- email (only to match them on the task board);
- which AI tool(s) they use: Claude Code, Cursor, Codex or Kimi;
- their OS: macOS, Linux or Windows.

Then:
- **approvers:** who may change priorities and accept reviews (default: product + tech lead);
- **question owner:** who gets triage's questions (default: tech lead);
- **merges to main:** who is the only person who merges to `main` (default: tech lead).

**B. Projects.** For each project, the task-board key (e.g. `BIL`), its name and a one-line
description. Per team in the project, the owner(s), and for each repo:
- the code-host key;
- `group/name`;
- the default branch;
- whether it uses a dev → main release flow;
- for a shared repo, the folder (`only:`) that belongs to the project.

**C. Task board.**
- Plane: cloud, or self-hosted on the agent host? The default is to use an existing Plane, and
  `infra/plane/` can install one.
- Its base URL and workspace slug.
- Other trackers need an adapter first: see `infra/adapters/README.md`.

**D. Code hosts.** For each one, a short key (`main`, `oss`…), its type (`gitlab` or `github`)
and its URL.

**E. The agent host.** The always-on machine that runs the service agents and agent-mcp:
- its OS: macOS (launchd) or Linux (systemd);
- where the repo will live (default `/srv/agent-ops`) and where the read-only repo clones go
  (default `/srv/repos`);
- the public URL people's agents use to reach agent-mcp (e.g. `https://agents.example.com/agent-mcp`).
  This needs a reverse proxy or tunnel, which the person sets up.

**F. Service agents.** Which ones to run: `triage`, `analyst`, `release`, `nightly`. Defaults: all
four, on the default schedules (triage every 5 min, analyst every 10 min, release every 15 min,
nightly at 02:30 local time, weekdays). Also:
- **Model per agent.** Defaults: Sonnet for triage, release and nightly; Opus for analyst.
- **Budget.** The spend cap per 5 hours at API prices (default $60).
- **Claude auth.** A Console API key, or a subscription token from `claude setup-token`.

**G. Optional data sources** the agents may read (read-only accounts only):
- Postgres;
- ClickHouse;
- Metabase;
- Jenkins;
- Figma.

**H. Notifications.**
- **Telegram bot:** yes or no. If yes, the person creates it with @BotFather; its token is a
  secret.
- **Alerts:** who gets agent alerts (default: the question owner).

## 2. Confirm the plan

Summarise the answers as a short plan:
- the files you'll write;
- the services you'll install;
- the secrets the person will type;
- the people who need to do something on their own machine.

Wait for "yes".

## 3. Write the team's config

From the answers, write:
- `routing.yaml`: `tracker`, `code_hosts`, `teams` (the default project), `projects` (the
  others), `approvers`, `question_owner`, `merge_to_main`.
- `projects.yaml`: every project with its `tracker_project_id`, filled in step 5.
- `people.yaml`, from `people.template.yaml`. It is git-ignored: it holds emails.
- `knowledge/lineage.yaml` (optional): metrics, page names and owners. See the example in the file.

Check: `python3 -I infra/bin/adapter repos` lists every repo you expect; `bash tools/lint.sh` is
clean.

## 4. Secrets on the agent host

**Env file.** The shared env file is `~/.config/agent-ops/env`, mode 600; every service agent
loads it. Write the non-secret values yourself, from `infra/env.template`:
- `TRACKER_BASE_URL`, `TRACKER_WORKSPACE`;
- the data-source hosts and users;
- `AGENT_MCP_PUBLIC_URL`.

**Secrets the person sets.** Give them one `bash tools/set-secret.sh ~/.config/agent-ops/env <NAME>`
line for each:

| Secret | What it is |
|---|---|
| `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` | Claude auth for the service agents |
| `TRACKER_TRIAGE_TOKEN`, `TRACKER_RELEASE_TOKEN`, `TRACKER_NIGHTLY_TOKEN` | One bot user per service agent on the task board, so their changes are attributable |
| `CODEHOST_<KEY>_TOKEN` | Read-only token per code host |
| `CODEHOST_<KEY>_RELEASE_TOKEN` | Only for the release agent. GitLab: `api` scope, Developer. GitHub: fine-grained, Commit statuses write + Pull requests read |
| `PG_RO_URL`, `CH_RO_PASSWORD`, `METABASE_AGENT_API_KEY`, `JENKINS_RO_AUTH`, `FIGMA_RO_TOKEN` | Read-only accounts for the data sources you chose |
| `TELEGRAM_BOT_TOKEN` | Only if you use Telegram |

**Revert tokens.** These belong to a person (usually the tech lead) and go in
`~/.config/agent-ops/revert.env`, mode 600, which no agent ever loads:
- `TRACKER_REVERT_TOKEN`;
- `CODEHOST_<KEY>_REVERT_TOKEN`.

**Check:** for each token, `python3 -I infra/bin/adapter tracker whoami` (or `codehost <key> whoami`)
prints the bot or person it belongs to, and nothing else.

## 5. The task board

1. **Self-hosting Plane on the agent host?** Run `bash infra/plane/install.sh` (macOS or Linux)
   and follow `infra/plane/README.md`. The person creates the instance admin and the workspace in
   the browser.
2. **Bot users.** Create the three bot users (triage, release, nightly) as workspace members. The
   person creates their tokens and sets them in step 4.
3. **Configure** with `bash infra/plane/configure.sh`. It is idempotent and never deletes. It
   creates each project from `projects.yaml`, with these states:
   - `Inbox`, `Backlog`, `Triaged`, `Todo`, `In Progress`, `In Review`;
   - `Ready for E2E`, `Findings`, `Done`, `Cancelled`.
   It also creates the labels (`team:*`, `effort:*`, `analysis:*`, `question`, `lesson`,
   `directive`, `source:*`) and adds the members from `people.yaml`.
4. **Project ids.** Write each `tracker_project_id` back into `projects.yaml`.

**Check:** `python3 -I infra/bin/adapter tracker projects` shows every project with `is_member: true`
for the triage bot.

## 6. The agent host

Set the answers from section 1F as environment variables on the install command:
- `AGENTS="triage:300 analyst:600 release:900"`, plus `NIGHTLY=true` with its times;
- `WAKE`, `AGENT_MCP` and `BACKUP` set to `true` or `false`;
- `TRACKER_BASE_URL` and `TRACKER_WORKSPACE`.

Each installer's header lists every setting. The first run installs everything with the schedules
off; check it, then run it again with `ENABLE_TIMERS=true`.

**macOS** (launchd, labels `io.agent-ops.*`):
1. Run `sudo bash infra/macos/srv-link.sh "$HOME/agent-host"` once, so `/srv` points into the home
   folder.
2. Run `bash infra/macos/install.sh`. It does the following:
   - clones the repo to `/srv/agent-ops` and the read-only repo clones to `/srv/repos`;
   - writes the env file skeleton;
   - renders the service agents' MCP configs;
   - installs the launchd jobs for the agents, nightly, wake, agent-mcp and backups.
3. Run `claude` once in `/srv/agent-ops` and accept the workspace trust prompt. The person does
   this: service agents ignore project settings in untrusted folders.

**Linux** (systemd units for a dedicated `agents` user): run `sudo bash infra/bootstrap.sh`, with
the same settings and parts. The nightly and backup times use systemd `OnCalendar` syntax.

**Both:**
- The person exposes agent-mcp (`127.0.0.1:8765`) at `AGENT_MCP_PUBLIC_URL` through their
  reverse proxy or tunnel. Show them `infra/agent-mcp/README.md`.

**Checks:**
- `curl -s http://127.0.0.1:8765/healthz` prints `{"ok": true}`.
- `curl -s -o /dev/null -w "%{http_code}" -X POST "$AGENT_MCP_PUBLIC_URL/journal"` prints `401`
  (reachable; it wants a token).
- `bash infra/bin/run-agent.sh triage` exits 0 and its log line says `ok` or `idle`.
- `infra/bin/lineage build` lists nodes.
- `infra/bin/traps list` shows the example checks.

## 7. Everyone's machines

Give each person the block for their tool. They paste it into their own agent: their tokens stay
on their machine.

**Claude Code** (any OS):
```
/plugin marketplace add <this repo's git URL>
/plugin install core@agent-ops
/plugin install role-<their role>@agent-ops
/plugin install <tool plugins they need, e.g. gitlab-tools@agent-ops, clickhouse-tools@agent-ops>
```
Claude Code asks for each plugin's settings and keeps the secrets in the OS keychain:
- `tracker_api_key` (their own task-board token);
- `tracker_base_url`, `tracker_workspace_slug`;
- `agent_mcp_url`, `role`.

**Cursor, Codex or Kimi** (any OS):
```
git clone <this repo's git URL> ~/.agent-ops/repo
uv tool install --editable ~/.agent-ops/repo/clients
agent-ops setup --client <cursor|codex|kimi> --role <role> --person <key> --tools <gitlab,github,postgres,clickhouse,jenkins,figma,metabase: the ones they use>
agent-ops check
```
Read `docs/clients.md` for what each client supports. Kimi's hooks fail open (a crashed or slow
hook lets the call through), so for Kimi users read-only credentials and the rendered deny rules
are the real protection. Tell them so.

**Check, in a new session on their machine:**
- `/mcp` (or the client's equivalent) shows `agent-mcp` and the task board connected;
- the `lineage` tool answers.

## 8. Final checks and summary

- Run `bash tools/run-tests.sh` (all offline) and `bash tools/lint.sh`.
- Run one tick of each chosen agent by hand and read its log line
  (`~/Library/Logs/agent-ops/<agent>/` on macOS, `/var/log/agents/<agent>/` on Linux).
- Run `bash infra/bin/nightly.sh --dry-run --skip-tests`, if nightly is on. It changes nothing on
  the board.
- Build the reference page: `uv run --with markdown --with pyyaml python docs/reference/build.py`.

Then give the person a summary:
- what runs where;
- the URLs;
- which secrets are set (names only);
- what each teammate still has to do;
- how to undo agent changes (`infra/bin/journal revert-plan`, then `revert-apply` in a terminal).
