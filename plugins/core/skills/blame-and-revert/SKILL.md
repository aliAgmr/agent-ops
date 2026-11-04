---
name: blame-and-revert
description: Find who changed something and undo it — the team's action journal (agent-mcp tools `blame`, `suspects`, `revert_plan`). Use when the user asks who/which agent changed a task, MR or field ("who moved BIL-12 back to Todo?", "which agent commented this?"), when something broke and you need the recent changes that touch it ("what changed in analytics.orders this week?", "why is this page wrong since Monday?"), or when the user wants an agent's or a run's changes undone ("revert what triage did to BIL-40", "undo my last session").
---

# Blame and revert (the action journal)

Every write any agent makes (a person's Claude, Cursor, Codex or Kimi, and the service agents) is
recorded in the team's **action journal** on the agent host: who (the person, their agent, or which
service-agent run), when, what it changed, the value **before**, and how to undo it. The core
plugin's `journal.sh` hook reports it; nothing to do on your side.

## Who changed it: `blame(target)`
- `target`: a task key (`BIL-123`), an MR/PR (`group/repo!12`) or another journal target.
- Each row: when, who, **how** ("by hand on the task board", "Noor's agent (cursor, frontend), session …",
  "service agent run triage-20261104T…"), what changed, and how to undo it.
- Changes made by hand on the task board come from the item's activity; everything an agent did comes from
  the journal. Give the user the rows that answer their question, not the whole list.

## What broke it: `suspects(project, term)`
- `term`: the broken thing's name: a table (`analytics.payments_ledger`), a
  file, an API route, a page or a metric. `since_days` defaults to 7.
- It also searches what the thing is built from (lineage: the views, tables and code files behind a
  page or route), marked `(upstream)`, so a broken page leads to the pipeline SQL or view that changed.
- Returns commits in the project's repos that add/remove that text or touch such a path (author,
  when, agent-assisted or not, on the default branch or not) and journal events that mention it,
  most likely first. These are **leads, not verdicts**: open the top ones and check before you say
  "this commit broke it".

## Where it comes from and where it goes: `lineage(node, direction)`
- `node`: a table or view (`analytics.orders`, `v_revenue_daily`), an API route (`GET /invoices/summary`), a page
  (`/revenue`), a metric (`metric:mrr`) or a file path. `direction`: `impact` (what changes
  if it changes: views, routes, pages, metrics) or `upstream` (what it is built from).
- Scanned daily from the repos and the warehouse catalog; owners come with it, so it also says who to ask.

## Undo it: `revert_plan(...)`
- Select by `event`, `run` (a service-agent run), `session` (a Claude session), or `target` and/or
  `actor` with `since_hours`.
- It is a **dry run**: it saves a plan and returns its steps. Nothing changes.
  - `auto` steps (task board fields, labels, assignees, comments, links, created items → Cancelled, MR/PR
    close/restore, MR/PR notes, commit statuses) are applied by a person on the agent host with
    `infra/bin/journal revert-apply <plan>`. A field someone changed again since is left alone.
  - `manual` steps (git pushes, CI, BI, Figma, artifacts) come with the exact undo.
  - Telegram messages can't be unsent.
- Show the user the plan, then tell them a maintainer applies it. **Never apply a revert yourself** and never
  write to the journal: the guard blocks it, and blame is only worth something if agents can't edit it.
