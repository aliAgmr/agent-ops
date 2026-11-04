---
name: prioritise
description: Review and reorder the board — set or change priority, plan the next cycle, explain why something moved. Use when an approver asks "what should we do next", "reprioritise", "plan the sprint/cycle", "what's urgent", or disagrees with a priority the triage agent set.
---

# Prioritise the board

Only approvers (routing.yaml `approvers`, e.g. Sam and Alex) change priority. Use the same rubric as
the triage agent so the numbers mean the same thing to everyone. Triage sets a default priority on
every task it moves to `Todo` (its notes say `triage default, not manually prioritised`); change it
whenever you like.

## Rubric

Score each task 1–3 on three questions:

| | 1 | 2 | 3 |
|---|---|---|---|
| **Impact** | nice to have, one user | one role's daily work | money decisions / many roles / wrong numbers in production |
| **Urgency** | no date | this month | this week, or a month-close / contractual deadline |
| **Blocking** | blocks nothing | blocks one task | blocks several tasks or another team |

Sum → priority: **8–9 urgent**, **6–7 high**, **4–5 medium**, **3 low**.
Wrong numbers on a page people make money decisions with are always at least `high`.

## Steps

1. List the open tasks (not `Done`/`Cancelled`) with their team, priority, state, age.
2. Flag: tasks whose priority is still the triage default (triage notes say `triage default`)
   and that look misjudged, `Blocked` tasks and what blocks them,
   people with more than 3 `In Progress` tasks, `urgent` tasks not started.
3. Propose changes as a table (task, old → new priority, one-line reason). Apply only what an approver confirms.
4. For each changed task, comment the reason (`[agent:sam-product] priority high → urgent: …`).
5. For a cycle plan: fill each person's capacity with the highest-priority unblocked tasks; leave
   ~20% free for bugs.
