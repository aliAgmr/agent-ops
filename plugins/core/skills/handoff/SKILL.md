---
name: handoff
description: Open a follow-up task for another team instead of doing work outside your own scope. Use when your task needs a design, an API, a table/query/job, a frontend change or a product decision that belongs to someone else, when you find a bug in another team's area, or when the user says "hand this to", "ask design/backend/frontend/data/product", or "open a task for".
---

# Handoff to another team

You only work inside your own scope (see `scope-guard`). Anything else becomes a new task board work
item that the triage agent routes. Never edit another team's repo, Figma file or data job.

## Who owns what

| Need | Team |
|---|---|
| What gets built, priority, business rules, acceptance criteria | product (e.g. Sam) |
| Page layout, components, states, copy placement, Figma | design (e.g. Riya) |
| API routes, DTOs, permissions, business logic, backend CI jobs, database schema | backend (e.g. Alex) |
| Frontend pages, API integration, UI behaviour | frontend (e.g. Noor) |
| Warehouse tables/views, BI questions, data jobs | data (e.g. Kenji) |

`routing.yaml` (`teams`, and `projects.<KEY>.teams`) is the source of truth for who that is.

## How

1. Search the task board first (duplicate check in `task-protocol`). If a task already covers it, comment there instead of creating a duplicate.
2. Create a work item (template from `task-protocol`) in `Inbox` with `source:agent`:
   - **Title:** what the other team must deliver ("API: expose overdue_amount on the invoices list").
   - **Goal:** why your task needs it.
   - **Context/links:** link your own task as the parent, plus everything they need (Figma frame,
     the API shape you expect, sample data, the query that is wrong).
   - **Acceptance criteria:** what "done" means *from your side*, testable.
3. On your own task: add a "blocked by" relation to the new one (`workitem_relation`, see
   "Relations" in `task-protocol`), comment the link, and move your task to `Blocked` if you can't
   continue without it.
4. Don't assign it, don't set the team or priority — triage does that.

## Typical chains

- New page: product task → design handoff → backend API task + data task (if new data) → frontend task.
- Design change after build: design → frontend (and backend if fields change).
- Wrong number on a page: frontend/product → data or backend (whoever owns the source), with the
  page, the expected value and how you know.
