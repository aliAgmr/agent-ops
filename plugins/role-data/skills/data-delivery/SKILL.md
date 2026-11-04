---
name: data-delivery
description: Deliver a data task end to end — define the number, write and validate the warehouse query, design the table/view, the CI job that refreshes it, and the BI question — as files and a merge request, with the DDL handed to a person to apply. Use for any data task, a new metric or table the backend or a page needs, a wrong number traced to the warehouse, or a change to a data job or BI question.
---

# Data delivery

Agents never write to the warehouse or run CI jobs (`scope-guard`). You produce **validated SQL,
files and a merge request**; the data lead (e.g. Kenji) or a maintainer applies DDL and runs jobs by
hand.

## 1. Define the number

Write in the task, before any SQL:
- Business definition in one sentence ("amount still owed on direct-sales invoices at the end of
  each day, in the account's currency").
- Grain of the output (one row per customer × day, …) and its key.
- Filters: which channel? which statuses? refunds? which time zone defines "a day"?
- Which existing metric spec it follows, if any.
If the definition is a business decision, ask an approver on the task (label `question`) and stop.

## 2. Write the query

- Follow `clickhouse-query-style` (or your warehouse's style skill): named CTEs, leading commas,
  `where true`, `FINAL`, the `-- agent:` attribution line.
- Check every source table against `data-traps` (grain, repeated totals, duplicates, paid vs
  remaining, mixed populations).
- Run it read-only through the warehouse MCP with `LIMIT`, on a small date range first.

## 3. Validate (paste the results in the task)

- Row count and key uniqueness: `count() = uniqExact(<key>)`.
- Reconcile one total against an independent source (another table, a BI question, finance's figure).
- Spot-check 2–3 entities by hand (one big, one small, one edge case: no data, negative, brand new).
- Note memory/time of the query (the warehouse's query log if needed) for anything that will run in a job.

## 4. Package

- **Table/view DDL** → a file in the data repo (`.sql`), merge request to its default branch. In the
  MR and the task: the exact DDL, the backfill plan, expected row count, who applies it.
- **CI job** → the job definition/script as a file in the MR. Never trigger or edit live.
- **BI** → `create_card` / `update_card_sql` via `metabase-tools` is allowed for saved questions
  (they don't write to a database). Put the card link in the task.
- If backend consumes it, post the output contract (table name, columns, types, grain, refresh
  time and its time zone) on the task and hand off the backend part.

## 5. Close

Move the task to `In Review` with the done comment: definition, validation numbers, MR, DDL to
apply, job schedule, BI link. Then stop: an approver reviews it and moves it on; never move it past
`In Review` yourself (not to `Ready for E2E` or `Done`; triage reverts it).
