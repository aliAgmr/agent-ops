Run one nightly pass. If the prompt ends with `DRY_RUN=true`, the board is read-only: do every step
the same way, but instead of each write, record it in `actions.json` with `"dry_run": true` and
word the report and messages as "would" (the guard blocks board writes anyway). `RUN_DIR`, the window
start (`SINCE`, UTC) and today's date are given at the end of this prompt. Work on every board in
`../../projects.yaml` (key = the board's identifier, `tracker_project_id` = its id); look up state and
label ids per project. A repo shared by several projects (routing.yaml lists it under each, usually
with `only:` folders): put work and findings in the project whose `only:` folder the change touches;
if nothing says, in the project of the task or person they match. Keep a running list of every board change you make
(`RUN_DIR/actions.json`, see step 8): append to it right after each write, so a crash leaves it
complete.

1. **Read** `RUN_DIR/facts.json`, `RUN_DIR/checks.json`, `RUN_DIR/traps.json` (data-trap
   checks), `RUN_DIR/lineage.json` (lineage problems), `RUN_DIR/waivers.json` (checks people
   keep waiving) and `RUN_DIR/signals.json` (rework in the service agents' runs), and the context
   files in CLAUDE.md.

2. **Other systems since `SINCE`** (read-only; keep each to a few calls):
   Skip a system your deployment didn't configure (its MCP server is absent) and say so in
   "Couldn't check".
   - **CI (Jenkins):** builds that ran in the window (job, result, who started it) and failed builds.
     Jobs that started failing are findings (step 6); routine green runs are not news.
   - **Warehouse (ClickHouse):** try `system.query_log` for DDL in the window (`query_kind` in Create,
     Alter, Drop, Rename; `type = 'QueryFinish'`; user, database, table, short query). If access is
     denied, say so and rely on the data repo's commits. Also list tables in the databases the app
     reads (`analytics` in the example) whose newest active part (`system.parts`) is older than 24 h:
     stale data is a finding.
   - **BI (Metabase):** `facts.json` → `metabase` lists questions and dashboards created or edited in
     the window, with their last editor. Run each edited question once (`run_card`) and note
     errors.

3. **Match work to tasks.** For every merged or open MR, direct commit (not a merge commit, not a
   `dev`→`main` release merge) and CI/warehouse/BI change in the window, find its task:
   the keys in it first; otherwise the open task whose title, description and assignee clearly
   describe the same change. Rate each match *sure* (a key, or an unmistakable description) or
   *possible*. Only *sure* matches change the board; *possible* ones go to the report.

4. **Bring tasks up to date.** For each task with *sure* evidence, in the
   item's own project, move it forward only:
   - an open MR or commits for it, task in `Todo` or `Triaged` → `In Progress`;
   - its MR merged into the repo's default branch (`dev`), task in `Todo`, `Triaged` or
     `In Progress` → `In Review`;
   - its work merged into `main` (or, for a repo with `release: false` in routing.yaml, into its
     default branch), task in any open state except `Blocked` → `Done`. Don't wait for E2E: the
     release agent covers that flow; you close what it missed.
   Comment once per move: `[agent:nightly] Moved to <state>: <MR link or commit> (<one line why>).`
   Never move items in `Inbox` (triage hasn't seen them), `Backlog` or `Findings`.

5. **Out-of-pipeline work.** Work done in the window that matched no task (*sure* or
   *possible*): a merged MR, a direct commit to a default branch or `main`, a new or changed
   warehouse table/view, a new or changed BI question/dashboard, a new or reconfigured CI job. Group pieces that are clearly one change (an MR and the view it adds). For each
   group, create one task in the right project, directly in state `Done`:
   - title: what was done, in plain words (not the MR title if that's cryptic);
   - description: what changed and where (links), who did it, when, and "Done outside the task
     pipeline; logged by the nightly agent.";
   - labels `out-of-pipeline` + the team label; assignee: the author (by email) if they're a member;
   - `external_source` `nightly-oop`, `external_id` = `<repo path>!<iid>` for an MR,
     `<repo path>@<short sha>` for a commit, `ch:<db.table>`, `mb:<card|dashboard>:<id>` or
     `jenkins:<job>` (+ `:<date>` when the same object can change again later).
   Skip routine things: dependency bumps by bots, formatting-only commits, scheduled job runs.

6. **Findings**, in state `Findings`, label `source:nightly`, unassigned,
   priority from severity (critical/high → `high`, medium → `medium`, else `low`):
   - from `checks.json`: failed setup/typecheck/lint/unit per repo (one task per repo+check,
     `external_id` `test:<repo>:<check>`), each vulnerable dependency (its `fingerprint`), each
     leaked secret (`fingerprint`, priority `urgent`), each semgrep result that's a real problem
     in context (read the code; skip clear false positives and say how many you skipped), a red
     nightly E2E (`e2e:<pipeline id>`), new BUGS.md entries;
   - from step 2: failing CI jobs (`jenkins-fail:<job>`), stale warehouse tables
     (`stale:<db.table>`), broken BI questions (`mb-broken:<id>`);
   - from `traps.json` (data traps): each check in `blocking` or `notify` (`trap:<id>`; its value, expect,
     `why` and tables; priority from its `severity`), and each `unreadable` check (`trap-unreadable:<id>`,
     priority `low`: a check that can't run is not a pass). Checks in `baselined` are known
     violations (`knowledge/trap-baseline.yaml`): not findings. Each check in `stale_baseline`
     passes again but still has a baseline entry (`trap-baseline-stale:<id>`, priority `low`:
     remove the entry so the violation can't come back unnoticed). From `waivers.json`: each id
     waived on 3+ MRs in 30 days (`waived-often:<id>`, priority `low`, naming the MRs: the check,
     not the code, probably needs changing). From `lineage.json`: each `problems`
     line (`lineage:<short slug>`, priority `low`: knowledge/lineage.yaml names something that no
     longer exists, so its owner should update the file). If any of these files has `error`, say
     so in the report instead;
   - **untracked follow-up work** a change implies but no task covers, e.g. a backend contract
     change with no frontend task, a migration or warehouse DDL with no handover, a BI
     question that reads a column that was just dropped (`followup:<short slug>`).
   **Group so the board stays readable:** vulnerable dependencies → one finding per repo
   (`external_id` `deps:<repo>`; a table of package, versions, advisory ids and max severity in
   the description; priority from the worst one; when the list changes, comment what was added or
   fixed); stale tables → one finding per database (`stale:<db>`), naming each table and its last
   update (many tables with the same timestamp usually mean one bulk reload, not staleness: say so
   rather than filing it); a failing check → one finding per repo and check.
   **Not findings:** failures that come from the sandbox itself (tests that need a database,
   network or service that `checks.yaml` deliberately doesn't provide, e.g. "connection refused
   127.0.0.1:…"). Mention them once in the report's tests line so `checks.yaml` can be adjusted.
   `external_source` is `nightly`. Before creating, look the `external_id` up: if a task exists
   and is still in `Findings`, comment `[agent:nightly] Still present on <date>.` (once per
   night); if it was moved on by a human, leave it. For a `Findings` task you created whose
   problem is gone tonight, comment `[agent:nightly] No longer found on <date>; close it if you
   agree.` once. Description: what, where (file:line, package@version, job, table), evidence,
   why it matters, the likely fix and owner team, with its certainty label from `findings-format`
   (`confirmed` with a severity, or `needs checking: <the missing fact>`). The report lists what
   didn't run tonight under **Not checked** (an `error` in any RUN_DIR file, a skipped repo).

7. **Stale and stuck work** (report only, no board changes): `In Progress` with no activity for
   3+ days, `In Review` waiting 2+ days, `Blocked` items (with what they wait on), MRs open 5+
   days, open tasks with no MR in the window and older than 5 days in `In Progress`, MRs with no
   task.

8. **Write the outputs** in `RUN_DIR` (UTF-8):
   - `actions.json`: `[{"project","key","action":"moved|created-done|created-finding|commented",
     "from","to","title","why","link"}]` — every board change, nothing else.
   - `report.md` (English; becomes the board page "Daily <date>"), with these sections:
     `# Daily <date>` · `## Summary` (5 lines max) · per project
     `## <KEY>` (e.g. `## BIL`) with `### Done`, `### In progress`, `### Moved by the nightly agent`,
     `### Out-of-pipeline work logged`, `### Stale and stuck` · `## Standup` (per person who did
     anything or holds open work: **Yesterday** / **Today (in progress)** / **Blocked**) ·
     `## Security and tests` (per repo: tests ✅/❌, dependencies, secrets, code scan; E2E;
     new findings with keys) · `## Data and infra` (CI, warehouse, BI) ·
     `## Agent runs` (from `signals.json`: one table row per agent with runs, failed, turn_limit,
     repeated_error, reread, skill_reread, blocked; then the `top` details seen in 2+ runs, one
     line each with the skill or file it points at and what to change: a clearer skill step, a
     missing permission, a flaky tool. Counts only: never paste tool output. If `error` is set,
     say so) ·
     `## Needs a human` (possible matches, anything you weren't sure about) · `## Couldn't check`.
     Link every task (`<TRACKER_BASE_URL>/<workspace>/browse/<KEY>/`, both given at the end of this
     prompt) and MR/PR.
   - `group-<KEY>.en.txt` per project (e.g. `group-BIL.en.txt`): the standup summary for that
     project's group chat **in English**, at most ~1200 characters, plain text (no Markdown): done
     yesterday, in progress, blocked/stale, new findings count, tests status in one line. End with
     the line `PAGE_LINK` (the sender replaces it with the page's URL). Skip a project with nothing
     to say by writing just `-` in its file. A project with no group chat gets its summary in its
     question owner's DM (routing.yaml), so write it for that person.
   - `dm.en.txt`: the question owner's full list, in English, plain text: every action from `actions.json`
     (grouped: moved, logged as out-of-pipeline, findings created, commented), each with key,
     title and why, then "Needs a human" and "Couldn't check". End with `PAGE_LINK`.

9. Finish with a one-paragraph summary: tasks moved, out-of-pipeline tasks created, findings
   created/updated, tests status, and anything you couldn't check. Start it with `ERROR:` only
   if the board or `facts.json` couldn't be read.
