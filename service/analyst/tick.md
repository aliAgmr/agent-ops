Run one analyst tick.

Boards: every project in `../../projects.yaml` (key = the board's identifier, `tracker_project_id`
= its id), or only those in TRIAGE_PROJECTS when the context below names them. Labels and states
have the same names on every board but different ids: look them up per project.
(infra/bin/precheck.py checks the same boards.)

1. List the work items in state `Inbox` with label `analysis:needed` on every board. If there
   are none, reply "nothing to do" and stop.
2. Take **one**: the oldest by `created_at` across all boards. Follow the `analysis` skill
   completely for it. Don't start a second item, even if turns are left: one deep analysis per run.
3. Write the board updates in the order the skill gives (the analysis section first, the label swap
   last), so a run that stops early never marks an item `analysis:done` without its analysis.
4. Finish with a one-paragraph summary: `<id> → bug|feature: <the finding or recommended approach in
   one line>; options <n>; questions <n> (blocking: <question id> | none); frames <n>; artifacts
   <titles> | none; unverified: <what> | nothing; check: <supported>/<total> supported, fixed <n>`
   (`check: not run` when the fresh check (skill step 6a) didn't run). If a board or a call you needed couldn't be read,
   start the summary with `ERROR: <what failed>` instead (see "Unattended-run rules" in CLAUDE.md).
