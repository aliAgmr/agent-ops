---
name: triage
description: The triage procedure for one task-board Inbox work item (plus backlog reviews and effort re-assessment) — understand, queue bugs and features for the analyst's deep analysis and route from it, investigate code and data read-only, write triage notes, route to team(s), set priority and effort, split, assign, move to Todo, or ask the question owner. Use for every Inbox item during a triage tick.
---

# Triage one work item

The item is on one of the boards in `projects.yaml` (the example team has **Acme Billing** `BIL`,
**Acme Pricing** `PRC` and **Acme Analytics** `ANL`). Same states, labels and rules on all; only the
ids differ, so look up state and label ids in the item's own project. Teams can differ per project:
the default project uses routing.yaml `teams`; another project uses `projects.<KEY>.teams` (a team
missing there doesn't exist in that project; a team may list several people). A project may also
set its own `approvers` and `question_owner`. Everything you create (children, question items,
relations) goes into the item's own project.

## 0. Lessons and directives (labels `lesson`, `directive`)

These Inbox items are records, not product work. Handle them with "Lessons and directives" below,
not with steps 1–7.

## 1. Understand

- Read the title, description, comments, links and parent. Follow Figma/MR/BI links if given.
- Is it the same work as a `Backlog` item (search those too)? Triage it normally, name the backlog
  item in the notes (ids in text), and comment on the backlog item:
  `[agent:triage] Now planned as <id>.` Don't move or change the backlog item itself.
- Is it a duplicate of an open item (search its own board; mention a likely match on another board
  in the notes, but don't link across projects)? → comment `[agent:triage] duplicate of <id>`, link
  it, add label `question`, assign to the question owner, leave in `Inbox`. Don't cancel it yourself.
- Is it too vague to route (no goal, or no way to know when it's done)? → ask (step 6).

## 1b. Deep analysis first (labels `analysis:needed`, `analysis:done`)

Bugs and features get a deep analysis from the analyst agent (`service/analyst`) before you route
them: root cause and fix options for a bug, a plan and approaches for a feature, with evidence. Only
on boards that have the two labels (infra/plane/configure.sh creates them).

- **Needs one:** a bug (something works differently from how it should, or a wrong number) or a
  feature (new or changed behaviour) whose effort you estimate at `s` or more, with no `analysis:*`
  label yet. Not: lessons, directives, questions, duplicates, chores (dependency updates, renames,
  config, access requests), items triage created itself (children), `Backlog` items.
  → add label `analysis:needed`, comment
  `[agent:triage] Queued for deep analysis (<bug|feature>, about <effort>). Routing waits for it.`
  and stop here for this item (it stays in `Inbox`; it counts towards the 5).
- **`analysis:needed`:** the analyst hasn't finished. Skip the item (not counted).
- **`analysis:done`** with an open question item linked from an `[agent:analyst]` comment: it waits
  for the answer (tick step 5). Skip it (not counted).
- **`analysis:done`, nothing open:** triage it with steps 2–7, using its `## Analysis` section (below).
- Too vague to analyse (no goal at all)? Ask first (step 6); queue it once the answer makes it clear.

## 2. Investigate (read-only, time-box ~10 tool calls per item)

If the item has `analysis:done`, its `## Analysis` section is the investigation: read it, check only
what routing still needs, and don't repeat it. Use its plan for the teams and their order, its fix or
approach recommendation for the children's scope, and its effort hints for the effort labels. If you
disagree with it, say why in the triage notes.

Look only as deep as needed to route and estimate. Search the repo clones with **one plain command
per Bash call** (no `;`, `&&`, `2>&1` or `cd`):
`git -C /srv/repos/<repo> grep -n -i <pattern> [-- <path>]`, list files with
`git -C /srv/repos/<repo> ls-files <path>` or `ls`, then `Read` the files you need. Only `git -C
/srv/repos/<repo>` followed by `grep`, `log`, `show`, `ls-files` or `blame` is allowed; plain
`grep`/`rg`/`find` and pipes into other tools are not. Start with `-- src` (or the relevant folder):
generated folders flood unrestricted searches.
`/srv/agent-ops/infra/bin/lineage upstream "<page path, route or view>"` (or `impact`) shows in one
call which views, tables, code files and pages are involved, and their owners: often enough to pick
the teams (when the team set up lineage; see `knowledge/lineage.yaml`).
- **Which repos:** routing.yaml says which repos belong to which project (`teams` for the default
  project, `projects.<KEY>` for the others). A repo entry with `only: <folder>/` means only that
  folder belongs to the project: search with `-- <folder>`. Don't search other projects' repos
  unless the item says it touches them. A team with no repo listed (e.g. a frontend in another
  team's repo the agents can't read): route its work from the description alone.
- **Backend:** grep the backend repo for the route, DTO, service or table named in the task. Note
  files and routes that would change.
- **Frontend:** find the page (e.g. under `src/pages/`) in the frontend repo.
- **Data:** check whether the needed numbers already exist: list tables / `DESCRIBE` in the
  warehouse, search the BI tool. Use small `SELECT … LIMIT 10` queries only, starting with the line
  `-- agent: triage`. Check `data-traps` if the task is about a wrong number.
- **Jobs:** read CI job configs/logs only if the task mentions a job or a refresh time.
- **Application database:** `information_schema` / small selects only if the task touches stored records.

## 3. Write the triage notes

Replace the `## Triage notes` section of the description:

```
## Triage notes  ([agent:triage] <date>)
- Teams: design → backend + data → frontend   (order of work)
- Touches: backend `src/modules/invoices/...` (route GET /invoices/...), table `analytics.invoices_daily`, page `src/pages/billing/...`
- Data exists?: yes — <table/column> | no — needs new <table/metric>
- Risks / unknowns: <e.g. definition of "paid" unclear; data trap "row grain" applies>
- Analysis: <see "## Analysis" (bug|feature); recommended: <option in a few words>> | none (<why not queued>)
- Effort: <xs|s|m|l|xl> — <one line: what drives it>   (see "Effort"; per child when split)
- Priority: <value> (triage default, not set by an approver) — impact <1-3>, urgency <1-3>, blocking <1-3>
```

If an approver had already set the priority (step 5), write `Priority: <value> (set by <name>,
kept by triage) — …` instead.

## 4. Route and split

- One team → set the `team:*` label, assign that team's person in the item's project (routing.yaml →
  people.yaml email → board member; see "Finding people" in CLAUDE.md). When a team lists several
  people, pick the one whose open work in that project is smaller.
- Several teams → keep this item as the parent (label `team:product`, assignee the product team's
  person) and create one child per team in `Todo`-ready shape (template from `task-protocol`,
  `source:agent`), each with its own acceptance criteria (from the analysis's plan and proposed
  criteria when there is one), team label, effort label, assignee, and `blocked by` relations in work
  order (`workitem_relation` create with `relation_type: "blocked_by"` on the later child, then
  `list` to confirm; see "Relations" in `task-protocol`). Give each child its own triage notes.
- Unclear between two teams → pick the one that owns the **source of truth** (data before
  backend before frontend), and mention the alternative in the notes.

## 5. Priority

Use the rubric (same as the product `prioritise` skill): Impact + Urgency + Blocking, each 1–3.
Sum 8–9 urgent, 6–7 high, 4–5 medium, 3 low. Wrong numbers on a page people decide on: at least `high`.
If an approver already set a priority, keep it and only comment if your score differs by 2+.

## 6. When you can't decide → ask the question owner

Create a work item in the original's project: title `Question: <short>`, label `question`, assignee
the project's question owner (routing.yaml `question_owner`, or `projects.<KEY>.question_owner`),
state `Todo`, description = the question(s) as a numbered list with the options you see and your
recommendation, plus a link to the original. Comment the link on the original and leave the
original in `Inbox` (it'll be retried when the question is answered).

## 7. Finish

- Set the effort label (see "Effort"): one `effort:*` on the item and on each child.
- Add `source:*` if missing, move to `Todo` (children too). There is no approval step: don't leave
  the item in `Triaged` (only exception: the assignee isn't a project member yet, see "Finding
  people" in CLAUDE.md).
- Comment on the item: `[agent:triage] routed to <team(s)>, priority <p> (triage default, not set by an approver), effort <e>. Ready to pick up in Todo; an approver can change the priority any time.`
  When an approver had set the priority, write `priority <p> (set by <name>)` instead.
- Never start work, never change code, never write to a database.

## Lessons and directives

**`lesson`** (an agent's mistake, recorded with the `lessons` skill; title `Lesson [<slug>]: …`):
1. **Normalise the slug.** List all `lesson` items in **every** project (any state): agent
   mistakes aren't project-specific. If another slug names the same kind of mistake, rename this
   item's title to use that slug. Keep slugs about the kind of mistake, not the instance.
2. **Count** the `lesson` items with this slug in every project (any state), this one included.
3. **Second occurrence or more**, with no open `Mechanism [<slug>]` item in any project, or only
   one that was `Done` before this lesson was created (the fix didn't hold): create
   `Mechanism [<slug>]: <stop this kind of mistake>` in this lesson's project.
   - Put it in `Todo`, with label `team:backend` and `source:agent`, assigned to the question owner
     (who owns agent-ops), at triage default priority `medium`.
   - Description: the lesson ids and one-line summaries, and the mechanism you'd suggest. Prefer a
     guard.sh rule or check first, then a test, then a skill rule. "Remember to…" is not a mechanism.
4. Comment on the lesson:
   `[agent:triage] lesson [<slug>], occurrence <n>; mechanism: <id | "not yet (first occurrence)" | <id> (already open)>`.
   Then move the lesson to `Done`. Recorded lessons are kept as Done items so they can be counted.

**`directive`** (a standing rule from an approver, recorded with the `directives` skill):
- Route it to the question owner: label `team:backend`, assignee the question owner, priority `high`
  unless an approver set one. Move it to `Todo` and comment
  `[agent:triage] directive routed to <name>: done when a change enforcing it is merged in agent-ops (link the MR/PR) or they close it with a reason.`
- If it duplicates an open directive, comment `[agent:triage] same rule as <id>` on both and leave
  this one in `Inbox` for the question owner (don't cancel).

**Reminder (every run, tick step 2c):** for open `directive` items (not `Done` or `Cancelled`)
created more than 7 days ago with no comment in the last 7 days, comment
`[agent:triage] directive open for <n> days: still needs a mechanism in agent-ops. @<question owner>`.

## Backlog items (tick step 2b)

`Backlog` holds parked ideas, often imported from another tool (a spreadsheet, a form). Whatever
imports them may own their title, description and priority. You review each one once, when it has
the label `backlog:to-review`:

1. **Understand and investigate** as in steps 1–2 above (same read-only rules, ~10 tool calls).
   The text is often a few words: work out what it most likely means from the code, data and
   other tasks, and say how sure you are.
2. **Overlaps:** search its board for open items, other `Backlog` items and items done in the last
   ~60 days that cover the same thing. Name real overlaps in the review comment (ids in text);
   never close, cancel, merge or move either item.
3. **Comment once** (check first that there's no `[agent:triage] Backlog review` comment yet):

   ```
   [agent:triage] Backlog review (<date>)
   - Probably means: <one or two lines; say if unsure>
   - What exists today: <routes, pages, tables, jobs, with paths>
   - Data exists?: yes — <table/column> | no — needs <…> | n/a
   - Likely teams: <data → backend → frontend …>   Effort: xs | s | m | l | xl (<what drives it>)
   - Overlaps: <id (open | backlog | done): how> | none found
   - Open questions: <what must be decided before it can be planned>
   ```
4. **Remove the label** `backlog:to-review` (keep every other label) and set the effort label from
   your review (see "Effort"). Change nothing else: not the state, priority, assignee, title,
   description or team labels, and no children.

## Effort

Every item triage assesses gets exactly one effort label. Effort is the hands-on work for the
assignee(s) to reach the acceptance criteria, including tests and review fixes, but not waiting time:

| Label | Effort |
|---|---|
| `effort:xs` | under 2 hours |
| `effort:s` | 2 hours to 1 day |
| `effort:m` | 1–3 days |
| `effort:l` | 3 days to a week |
| `effort:xl` | more than a week: say in the comment that it should be split, and how |

- **Split items:** each child gets the effort of its own team's part; the parent gets the effort of
  the whole (roughly the children added up, plus hand-over), so a parent is never smaller than its
  biggest child.
- **Base it on what you found** in steps 1–2 (files and routes that change, whether the data
  exists, migrations, new pages, unknowns). When unsure between two sizes, pick the bigger one and
  say why. Always set one: a best guess with "unsure" in the reason is better than none.
- **No effort label** on `lesson`, `directive` or `question` items, or on items in `Findings`,
  `Done` or `Cancelled`.
- **Changing it:** remove the old `effort:*` label and add the new one in the same update (never
  two effort labels), and change the `Effort:` line of the triage notes if there is one.
- **Who set it:** read the item's activity (`workitem_activity` list, field `labels`). The label
  was set by hand when the latest activity that added or removed an `effort:*` label has an actor
  other than the triage bot (people.yaml `service.tracker_triage_bot`; import jobs that use the same
  token count as the bot). Then **keep the person's choice**, also when they removed the label:
  never change, re-add or remove it. If your estimate differs, comment once (check you haven't
  already said the same since their change): `[agent:triage] Effort was set to <label | none> by
  <name>; my estimate is now <label> because <reason>. Not changed.`

## Effort updates (tick step 2e)

Triage re-assesses the effort of an item when its scope may have changed. Candidates: items in
`Triaged`, `Todo`, `In Progress`, `In Review`, `Ready for E2E`, `Blocked`, or `Backlog` without
`backlog:to-review` (not `lesson`, `directive` or `question` items), updated since the last
successful run, where since that run a person (any actor except the triage, release and nightly
bots) did one of these:
- changed the title (`name`) or description (activity fields `name`, `description`);
- added a comment (`workitem_comment` list: newer than the last run, not by a bot);
- added or removed a child (field `parent` on the child; the activity's old and new values name
  the old and new parent, so re-assess both). A new child created in `Inbox` is triaged normally;
  when you triage it, re-check its parent's effort as well.

Take at most 5 per tick (oldest change first). For each:
1. Read what changed: the activity entries and new comments since the last run, the current
   title, description, triage notes and children. Investigate only if the change needs it
   (~5 tool calls).
2. Estimate again with the table above. If the item has children, the parent follows them.
3. Label set by triage (or no label yet): if your estimate changed, swap the label, update the
   `Effort:` line, and comment `[agent:triage] Effort <old> → <new>: <what changed and why>.` If it
   didn't change, change nothing and don't comment.
4. Label set by a person (see "Who set it"): don't touch it; comment only if you disagree.
5. Change nothing else: not the state, priority, assignee, team labels, title or description.

## Effort backfill (tick step 2f)

Open items from before effort labels existed: items in `Triaged`, `Todo`, `In Progress`,
`In Review`, `Ready for E2E`, `Blocked`, or `Backlog` without `backlog:to-review`, with no `effort:*`
label (not `lesson`, `directive` or `question` items). Skip an item whose effort label a person
removed (see "Who set it"). Take at most 5 per tick (oldest first): read the item, its triage notes
or backlog review, comments and children; investigate only if needed (~5 tool calls); set the
label; comment `[agent:triage] Effort <label> (<range from the table>): <one-line reason>.`
Children before their parent when both are missing. Change nothing else.

## Review/Done rules (tick step 6)

Most task boards can't restrict who changes a state, so triage enforces it, on every board. The
project's approvers review every task: routing.yaml `approvers` (or `projects.<KEY>.approvers`).

| Move | Allowed only for |
|---|---|
| any state → `Done` | the approvers, the release agent and the nightly agent (people.yaml `service.tracker_release_bot`, `service.tracker_nightly_bot`) |
| `In Review` → `Ready for E2E` | the approvers (routing.yaml → people.yaml emails) |
| any state → `Cancelled` | the approvers |

Your own moves (the triage bot, `service.tracker_triage_bot`) don't count: closing a question item
(tick step 5) and the reverts below.

1. **Window:** only state changes after the last successful run count, and, when the context names
   `PRECHECK_RULES_SINCE` (the time the rules were rolled out on a board with history), none before
   it, even on a full run with no last-run time.
2. In each project, list items in any state updated since that time. For each, list its activity
   (`workitem_activity`, action `list`; page through all of it). A state change has field `state`,
   `old_value`/`new_value` = state names, `old_identifier`/`new_identifier` = state ids, `actor` =
   the user id; map state ids to names with that project's states. Match actors to emails with the
   `member` list (see "Finding people" in CLAUDE.md).
3. A move is forbidden if it matches a row above and the actor isn't allowed. If the activity has
   no actor, treat it as forbidden.
4. For each forbidden move, if the item is still in the state that move set: move it back to the
   activity's old state (`workitem` update with the old `state` id), then comment exactly:
   `[agent:triage] Only <approvers' names> (or the release or nightly agent for Done) can move a task to <state>. Moved back to <old state>; @<approvers> please review.`
   Mention the approvers by email-matched member as in other comments. If the item has moved on
   since (someone else already changed it), don't move it; comment the same line with
   "Not moved back: it is now in <current state>." instead of "Moved back to …".
5. Revert only the state. Don't change labels, assignees or anything else, and never delete,
   archive or reopen other items. Count the reverts for the tick summary ("moves reverted").
