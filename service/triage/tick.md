Run one triage tick.

Boards: every project in `../../projects.yaml` (key = the board's identifier, `tracker_project_id`
= its id), or only those in TRIAGE_PROJECTS when the context below names them. Do steps 1–6 on all
of them. States and labels have the same names on every board but different ids: look them up per
project, and keep every update, child, question item and comment in the item's own project.
(infra/bin/precheck.py checks the same boards.) A project may have its own approvers and question
owner: routing.yaml `projects.<KEY>.approvers` / `question_owner` win over the top-level ones.

1. List work items with state `Inbox` on every board, oldest first (across all). Also list items in
   state `Backlog` with label `backlog:to-review` (step 2b).
   If there are none of these, still do steps 2c, 2e, 2f and 6, then reply "nothing to do" if
   none of them changed anything, and stop.
2. Take at most 5 Inbox items this tick (all boards together). For each one, follow the `triage`
   skill completely. Items labelled `analysis:needed` are waiting for the analyst agent: skip them
   (they don't count). Bugs and features of effort `s`+ get that label first ("1b" in the skill)
   and are routed in a later tick, once they have `analysis:done`.
2a. Inbox items labelled `lesson` or `directive` count towards the 5 but follow "Lessons and
   directives" in the `triage` skill instead of the normal triage.
2b. Backlog reviews: then take at most 3 `Backlog` items labelled `backlog:to-review` (oldest first)
   and follow "Backlog items" in the `triage` skill: one review comment each, remove the label,
   change nothing else. Never route, assign, prioritise, split or move a `Backlog` item.
2c. Directive reminders: see "Lessons and directives" in the `triage` skill (every run).
2e. Effort updates: follow "Effort updates" in the `triage` skill: at most 5 open items whose
   title, description, comments or children a person changed since the last successful run get
   their effort re-assessed. A label a person set by hand is kept (comment only if you disagree).
2f. Effort backfill: follow "Effort backfill" in the `triage` skill: at most 5 open items with no
   `effort:*` label get one.
3. After each item, write the board updates before moving to the next item (so a crash leaves
   finished work behind).
4. (No step: triaged items go straight to `Todo`; there is no approval badge.)
5. Answered questions: for open `question` items assigned to the project's question owner that have
   a new comment from that person, move the linked original item back into processing (it is still
   in `Inbox`) and close the question item (state `Done`) with a one-line summary of the answer. If
   the original has `analysis:done`, swap it back to `analysis:needed` instead (remove
   `analysis:done`, add `analysis:needed`; comment
   `[agent:triage] <question id> answered: back to the analyst to update the analysis.`), so the
   analyst folds the answer in before you route it.
6. Review/Done rules: follow "Review/Done rules" in the `triage` skill. In short: for items (any
   state) updated since the last successful run (and, when the context names one, not before
   PRECHECK_RULES_SINCE), read their activity (`workitem_activity` list). A state change in that
   window is forbidden when it is → `Done` by anyone except the project's approvers, the release
   agent or the nightly agent; `In Review` → `Ready for E2E` by anyone except the approvers; or →
   `Cancelled` by anyone except the approvers.
   For each forbidden move: move the item back to the state it came from (the activity's old state)
   and comment `[agent:triage] Only <approvers' names> (or the release or nightly agent for Done) can
   move a task to <state>. Moved back to <old state>; @<approver>… please review.` Never touch a
   move made before PRECHECK_RULES_SINCE.
7. Finish with a one-paragraph summary: items triaged (id → team, priority, e.g. `PRC-3 → backend,
   high, s`), items queued for analysis (id → bug|feature), lessons recorded (slug → occurrence,
   mechanism id), directives routed or reminded, backlog items reviewed, effort set or changed
   (id → effort, e.g. `BIL-12 s → m`; kept: ids where a person's label was kept), questions opened,
   moves reverted. If a board or a call you needed couldn't be read, start the summary with
   `ERROR: <what failed>` instead (see "Unattended-run rules" in CLAUDE.md).
