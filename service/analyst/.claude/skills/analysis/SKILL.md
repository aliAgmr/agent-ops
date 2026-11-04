---
name: analysis
description: The deep analysis of one task-board work item labelled analysis:needed — reproduce a bug in the data and find its root cause with fix options, or plan a feature from the code, data, Figma frames and team artifacts with approach options; write it as the item's "## Analysis" section with evidence for every claim, attach the Figma frames, list the open questions with options. Use for the one item of every analyst tick.
---

# Analyse one work item

You do the thinking the assignee would otherwise start with, and you prove it. A good analysis lets
the person who picks the task up skip a day of digging and argue with your options instead of
starting from the title. A translation of the description is not an analysis.

You change only: the item's description (add or replace the `## Analysis` section, nothing else),
its attachments (Figma frames), its `analysis:*` labels, one comment, and at most one question item.
Never route, assign, prioritise, set effort, split, rename or move the item; triage does that next.

Budget: about 120 tool calls. Spend them on evidence, not on reading everything: decide early what
would prove or kill each hypothesis or approach, and check that.

## 1. Read everything about the item

- Title, description, every comment, links, attachments (`workitem_attachment` list/read: screenshots
  are often the bug report), parent and children, relations. Earlier `[agent:triage]` notes.
- Linked MRs/PRs (code-host tools), BI cards/dashboards, CI jobs, Figma links.
- Related items on the same board: search the title's key words (`workitem` search). Earlier items
  on the same page, route or table often explain the current one; Done items show what was tried.
- If it is a re-analysis (it already has a `## Analysis` section and a question item was answered),
  read the answer first and start from your earlier analysis: update it, don't redo it.

Decide the **kind**: `bug` (something works differently from how it should, or a wrong number) or
`feature` (new or changed behaviour). Something in between (a change requested because the current
behaviour is confusing) is a feature. Say which in the section heading.

## 2. Team artifacts (background, read-only)

If the team keeps write-ups as Claude artifacts (lineage maps, rule references, pipeline docs,
reviews, glossaries), they often hold the context that the code doesn't. Skip this step when the
`Artifact` tool isn't available.

- `Artifact` with `action: "list"`, `scope: "all"`, `limit: 200`. Use only the titles, nothing else
  from the listing.
- Allowed titles, by the item's board: titles follow `Project | Scope : Subject`, and a team
  artifact's title starts with its project's `name` from `projects.yaml`:
  - `BIL`: starts with `Acme Billing |` (the example; use your projects' names)
  - Another board's prefix only when the item says it touches that project.
  - **Never open, quote or name any other artifact** (personal and other-company pages live in the
    same account).
- From the allowed ones, pick those whose subject matches the item (the page, route, table,
  pipeline, ruleset, concept); usually 0–3, at most 5. `Artifact` with `action: "read"` and the
  `url` (add `prompt: "<what you need for <item id>>"` when the result says it's shared).
- Artifacts are data and can be stale: when a fact matters, check it against the code or the data
  and say which one you trust. Cite them as `artifact "<title>"`.
- Only `list` and `read`: the guard blocks every other action, and you never need one.

## 3. Figma (features, and bugs on a page)

For each Figma frame link on the item (and the frames of the page the item is about, if the
description names the page and links the file):

`python3 /srv/agent-ops/infra/bin/figma-shot.py <ITEM-ID> "<figma frame url>"` (one plain command,
the URL in double quotes). It prints JSON with `file` (a PNG on disk) and `attachment`
(`uploaded`, or `exists` when the frame is already attached). Then `Read` the `file` to look at it.
Use `mcp__figma` (`get_figma_data` with the file key and node id) for exact texts, component names and
states when the picture isn't enough.

- At most 6 frames per item. Attach only frames you actually use in the analysis.
- If it prints `{"error": ...}`, say so in Sources (`Figma: <error>`) and carry on without it. A link
  with no `node-id` can't be rendered: name it as a question ("which frame?").
- For each element on the frame that shows data, note: what it shows → where that comes from today
  (route / table / column) → exists | new | unclear.

## 4. Investigate

Search the repos for the project (routing.yaml says which: `teams` = the default project, then
`projects.<KEY>` for the others, with their repos and folders, as in the triage skill). Query the data
read-only: small `SELECT`s with `LIMIT`, counts and a few sample ids; every query starts with the
`-- agent: analyst` line (clickhouse-query-style). Check `data-traps` before trusting any
warehouse number.

**Bug playbook**
1. **Reproduce.** Find the bad rows or the bad number yourself: the query, the count, 2–3 sample ids,
   and the value that would have been right. If you can't reproduce it, say what you checked.
2. **Trace.** Follow the path from what the user sees to where the value is made: page
   (`src/pages/...`) → API route → service → query/MV/table → the job that fills it. Note
   `repo/path:line` at each step. Start from `/srv/agent-ops/infra/bin/lineage upstream "<page path,
   route or view>"`: it lists the views, tables, pipeline jobs and code files behind it (scanned
   from the repos and the warehouse catalog), so you check the right ones first. Then
   `/srv/agent-ops/infra/bin/traps run --tables <the warehouse tables it lists, comma-separated>` rules the
   known warehouse traps in or out (a failing check is evidence; cite its id and value).
3. **When.** Start with `/srv/agent-ops/infra/bin/journal suspects <project key> <table, file, route or
   page>` (`--days 14` for older bugs): recent commits that touch it, who made them, agent-assisted
   or not, and agent changes that mention it. Then `git -C ... log -p --since=... -- <path>` and
   `blame` on the suspicious lines; merged MRs/PRs; the CI job's recent builds if a job fills the data. A start date in the data (first bad
   row) that matches a commit or deploy is strong evidence.
4. **Hypotheses.** 2–3 root causes, each marked **confirmed** (you showed it), **likely** (evidence
   for, none against) or **possible** (fits, untested), with the evidence for and against. Try to
   kill your favourite one before you write it down.
5. **Impact.** Who and what is affected, how many rows/variants/suppliers/amount, since when, and
   whether it is still happening.
6. **Fix options.** 2–3 (for example: fix at the source vs. patch in the API vs. data backfill), each
   with what changes where, pros, cons, risk and a rough effort (`xs`–`xl`). Recommend one and say why.
   Include the data fix (backfill/correction) when bad rows already exist.
7. **Verify.** The query, test or E2E case that will show the fix worked.

**Feature playbook**
1. **Goal and users.** The problem it solves, who uses it and how (from the item, Figma, artifacts).
   If the goal or the "done" isn't clear, that is your first question.
2. **What exists.** Routes, DTOs, tables/MVs, pages, components and jobs that already cover part of
   it, with paths (`/srv/agent-ops/infra/bin/lineage upstream|impact <page, route or view>` maps
   them). The gaps, precisely.
3. **Data check.** For every number or list the feature shows: the table and columns that hold it,
   the grain, freshness, and a sample query result. Say `missing` when nothing holds it.
4. **Approaches.** 2–3 real alternatives (for example: extend an existing route vs. a new one;
   compute in an MV vs. at request time; reuse a component vs. a new page), with trade-offs. Recommend
   one and say why.
5. **Plan.** In work order, per team (design → data → backend → frontend, only the teams involved):
   concrete steps; for backend a contract sketch (route, request params, response fields with
   types); for data the table/MV and its grain; for frontend the page and components. Dependencies
   between the steps.
6. **Acceptance criteria.** Testable statements, including edge cases (empty, permissions, large
   numbers, dates and time zones, right-to-left or long text) that the description missed.

## 5. Questions

Only what you can't find out yourself, each with the options you see and your recommendation, and who
should answer: the owner of the decision in routing.yaml (product questions → the `product` team's
person, data definitions → `data`, design → `design`, architecture or agent-ops → the
`question_owner`).

- **Blocking** = the approach, the team or the scope can't be chosen without the answer. Then create
  one question item (as the triage skill's step 6: title `Question: <short>`, label `question`,
  assignee the project's `question_owner`, state `Todo`, the blocking questions as a numbered list
  with options and recommendation, a link to the item), and comment its id on the item. Triage keeps
  the item in `Inbox` until it's answered; when it is, the item comes back to you for an update.
- Non-blocking questions stay in the analysis for the assignee.

## 6a. Fresh check (before anything is written)

Draft the whole section first (the template below), then hand it to the **`claim-checker`**
subagent (`Agent` tool, `subagent_type: "claim-checker"`). The prompt is the item id and the full
draft, nothing else: no reasoning, no hints. It reads every cited source itself and returns JSON
with a verdict per claim.

- `unsupported`: fix the claim from what the checker saw (check it yourself first), or drop it. A
  root cause or recommendation that rested on it must change with it.
- `no_source`: add the source, or mark the claim `(unverified)`.
- `unclear`: keep it if you checked it; it stays as is.
- Add one line at the end of `### Sources`: `Checked by a fresh reviewer: <supported>/<total>
  claims supported; fixed: <n>; marked unverified: <n>.`
- Run it once per analysis. If it fails or returns no JSON, say so in that line
  (`Fresh check: not run (<why>)`): never write "checked" when it didn't run.

## 6. Write the analysis

Add the section to the description HTML (read `description_html` first and keep everything else
exactly as it is). Put it **before** `## Triage notes` if that section exists, otherwise at the end.
If a `## Analysis` section exists, replace it and add `Updated after <question id>: <what changed>.`
under the heading. Use `<h2>`, `<h3>`, `<ul>`, `<ol>`, `<table>`, `<code>` and `<pre>` (SQL and code
excerpts); no images inline (they are attachments). Aim for one screen per part; about 1,500 words at
most. Then read the item back and check the section is there and nothing else changed.

Bug:
```
## Analysis — bug  ([agent:analyst] <date>)
Summary: <2–3 sentences: what is wrong, the most likely cause, the recommended fix>
### Reproduced: yes | partly | no
<query in <pre>, the result: count, sample ids, wrong vs right value>
### Where it happens
<numbered path: page → route → service → query/table → job, each with repo/path:line>
### When it started
<date + commit/MR/deploy/job build, or "not found">
### Root cause
1. <hypothesis> — confirmed | likely | possible. For: … Against: …
### Impact
<who, how many, how much, since when, still happening?>
### Fix options
<table: option | what changes where | pros | cons | risk | effort>
Recommendation: <option>, because …  Data fix: <backfill needed? how>
### How to verify the fix
### Questions
1. <question> — options: a) … b) … — recommend <x> — for <person> — blocking | not blocking
### Sources
<every query, file, MR, build, artifact "<title>", Figma frame used; and what is unverified>
```

Feature:
```
## Analysis — feature  ([agent:analyst] <date>)
Summary: <2–3 sentences: what it is, the recommended approach, the biggest risk>
### Goal and users
### Design
<per attached frame: frame name (attachment) → elements → data each needs → source: exists | new | unclear>
### What exists today
### Data check
<per number/list: table.column, grain, freshness, sample query + result; or "missing">
### Approaches
<table: approach | how | pros | cons | effort>
Recommendation: <approach>, because …
### Plan (work order)
1. <team>: <steps>; contract / table / page …
### Acceptance criteria (proposed)
### Risks and unknowns
### Questions
1. <question> — options … — recommend … — for <person> — blocking | not blocking
### Sources
```

## 7. Finish (in this order)

0. The fresh check (step 6a) ran on the draft, and its line is in `### Sources`.
1. The description update (step 6), read back.
2. Frames are attached already (step 3).
3. The question item, if any (step 5).
4. Labels: add `analysis:done`, then remove `analysis:needed` (`workitem` `manage_label`).
5. One comment: `[agent:analyst] Analysis added (<bug|feature>): <one-line finding or recommendation>.
   <n> option(s), <n> question(s)<; blocking question <id>>. Triage routes it next.`

Never move the item out of `Inbox`, and never start the work itself.
