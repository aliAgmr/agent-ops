# Nightly agent

Once a night, on the days you choose, the nightly agent looks at everything the team did since its
last pass on every project's board and repos. It brings the task board in line with that work,
files what's missing, runs the security and test checks, and writes the report the team reads at
standup.

| When (host local time, set in SETUP.md) | Job | What |
|---|---|---|
| `NIGHTLY_AT` + `NIGHTLY_DAYS` (macOS) / `NIGHTLY_ONCALENDAR` (Linux) | `<prefix>.nightly` / `nightly.timer` → `infra/bin/nightly.sh` | collect → checks → agent → report page |
| `NIGHTLY_SEND_AT` + `NIGHTLY_SEND_DAYS` / `NIGHTLY_SEND_ONCALENDAR` | `<prefix>.nightly-send` / `nightly-send.timer` → `infra/bin/nightly-send.sh` | Telegram: a summary per project, the full list to one person |

`<prefix>` is `LABEL_PREFIX` (default `io.agent-ops`). The window is always "since the last good
pass", so the first pass after a weekend covers the whole weekend. The run directory is named after
the date in `NIGHTLY_TZ` (default the host's time zone).

## Steps (all files in `RUN_DIR` = `/srv/nightly/<date>/`, mode 700)

1. **`collect.py` → `facts.json`** (no model), through the tracker and code-host adapters
   (`infra/adapters`). Per project: `tracker` = tasks changed in the window with their
   state/assignee history, every open task, the members; `repos` = per repo in `routing.yaml`
   (any code host in `code_hosts`, GitLab or GitHub) its `host` key, merge/pull requests updated,
   ones open 5+ days, and commits on the default branch and `main`, each with the task keys in
   them. Metabase edits by the team too, when `METABASE_URL` is set. A board or repo it can't read
   is listed in `errors`; the rest is still collected.
2. **`checks.py` → `checks.json`** (no model), per repo in `checks.yaml` `repos`:
   - a private checkout fetched straight from the code host into the platform cache folder
     (`agent-ops/nightly`), authenticated with `CODEHOST_<KEY>_TOKEN` as an HTTP header (never on
     a command line);
   - `setup` + each `checks` command in a throwaway **Docker** container with only the repo
     mounted (installs with `--ignore-scripts`; no secrets, no host env);
   - **osv-scanner** (vulnerable dependencies, one entry per package), **gitleaks** (secrets in
     the window's commits, redacted), **semgrep** `p/security-audit` + `p/secrets` (files changed
     in the window);
   - the scheduled E2E run from `checks.yaml` `e2e` (a GitLab pipeline schedule or a GitHub
     Actions workflow run) and each repo's `bugs_file`.
   Every finding has a stable `fingerprint`.
3. **The agent** (`service/nightly`, `run-agent.sh nightly`, ≤300 turns, the shared cost budget
   and circuit breaker): also reads CI, warehouse DDL and stale tables (if its read-only user may),
   and BI edits. Then:
   - **moves tasks forward** with evidence (MR/PR open → In Progress, merged into the integration
     branch → In Review, merged into `main` → Done), one comment each citing the MR/PR;
   - logs **out-of-pipeline** work (merged MRs/PRs, direct commits, warehouse/BI/CI changes with no
     task) as tasks created directly in `Done`, label `out-of-pipeline`;
   - files **findings** in the `Findings` state (label `source:nightly`, unassigned): failed
     tests, vulnerable packages, leaked secrets, real semgrep issues, red E2E, failing CI jobs,
     stale tables, broken BI questions, and follow-up work a change implies but no task covers.
     `external_id` = the fingerprint, so a finding is never filed twice; it gets a "still present"
     or "no longer found" comment instead;
   - writes `actions.json`, `report.md`, `group-<KEY>.en.txt` per project, `dm.en.txt`.
   It never moves `Inbox`, `Backlog`, `QA` or `Findings` tasks, never moves to `Ready for E2E` or
   `Cancelled`, never moves backwards, and never deletes anything.
4. **`publish.py` → `page.json`**: `report.md` as the task-board page "Daily <date>" in one
   project (`NIGHTLY_REPORT_PROJECT`, default the first in `projects.yaml`). Publishers live in
   `PUBLISHERS`; Plane is built in. Plane's public API has no pages, so it signs in to Plane's web
   app as the nightly bot (email + password) and uses the app's own pages endpoint. If that breaks
   (e.g. after a Plane upgrade), or your tracker has no publisher, the messages still go out with
   the reason instead of a link.
5. **`send.py`**: per `checks.yaml` `send`, each project's summary to its topic in the team's
   Telegram group (`topic`; group = people.yaml `service.telegram_group_chat_id`) or to one
   person's private chat (`dm`), and the full list to `dm_to` (`question_owner` = the person named
   in `routing.yaml`). Once per night (`sent.json`). No report → an alert to that person.

Triage treats the nightly bot like the release bot: it may move tasks to `Done`
(`service.tracker_nightly_bot` in people.yaml).

## Configuration

`checks.yaml` (SETUP.md writes yours; `NIGHTLY_CHECKS` points at another file):

```yaml
repos:                         # keys = repo names under /srv/repos
  billing-backend:
    branch: dev
    image: oven/bun:1.3-slim
    setup: bun install --frozen-lockfile --ignore-scripts
    checks: {typecheck: bun run typecheck, unit: bun test}
    bugs_file: tests/e2e/BUGS.md
  warehouse: {branch: main, only: models/}   # scanners only, no container
e2e:                           # optional
  host: main                   # a routing.yaml code_hosts key
  repo: acme/billing-backend
  branch: dev
  job_match: e2e
send:
  dm_to: question_owner        # or a people.yaml key
  projects:
    BIL: {topic: 101}          # Telegram forum topic id in the team group
    ANL: {dm: kenji}           # a person's private chat
```

Environment (the host's env file): `TRACKER_NIGHTLY_TOKEN` (or the file in
`TRACKER_NIGHTLY_TOKEN_FILE`, default `~/.config/agent-ops/tracker-nightly.token`; collect falls
back to `TRACKER_TRIAGE_TOKEN`), `CODEHOST_<KEY>_TOKEN` (read-only, one per `code_hosts` key, upper
case with `-` → `_`), `TRACKER_BASE_URL`, `TRACKER_PUBLIC_URL`, `TRACKER_WORKSPACE`,
`TRACKER_NIGHTLY_EMAIL`, `TRACKER_NIGHTLY_PASSWORD_FILE` (default
`~/.config/agent-ops/tracker-nightly.password`), `NIGHTLY_REPORT_PROJECT`, `NIGHTLY_TZ`,
`TELEGRAM_BOT_TOKEN`, and optionally `METABASE_URL` / `METABASE_AGENT_API_KEY`.

## Setup

1. A nightly bot account on the task board: invite it as a workspace member, sign it up, create a
   personal access token. Save the token and password in `~/.config/agent-ops/tracker-nightly.token`
   and `~/.config/agent-ops/tracker-nightly.password` (mode 600) and set `TRACKER_NIGHTLY_EMAIL`.
   For Plane, `infra/plane/configure.sh` then adds it to every project and creates the `Findings`
   state and the labels `source:nightly`, `out-of-pipeline`.
2. Tools: Docker running (Desktop on macOS, the engine on Linux); gitleaks, osv-scanner and
   semgrep on `PATH` (`brew install gitleaks osv-scanner semgrep`, or your package manager); the
   images named in `checks.yaml` (Prisma needs a non-slim Node image, for openssl).
3. macOS: `NIGHTLY=true bash infra/macos/install.sh` (with the other flags you keep on) loads both
   jobs. Linux: `NIGHTLY=true` in `infra/bootstrap.sh` renders `nightly.timer` and
   `nightly-send.timer` from `infra/systemd/`.

Try it: `AGENT_OPS_ENV=~/.config/agent-ops/env bash infra/bin/nightly.sh --no-agent` (facts and
checks only), `uv run --script infra/nightly/send.py /srv/nightly/<date> --dry-run` (prints the
messages). Logs: `~/Library/Logs/agent-ops/nightly/` on macOS, `journalctl -u nightly` on Linux,
plus `passes.log`, `sends.log`, `error.log` and the agent's `<date>.jsonl`. Tests (also in CI):
`uv run --no-project --with pyyaml --with requests --with markdown python tests/nightly.test.py`.

`run-agent.sh nightly` is only started by `nightly.sh`, with `AGENT_PRECHECK=off`; `precheck.sh
nightly` always answers "work" (no idle skip for the nightly pass).
