---
name: figma-to-tasks
description: Read a Figma page or frame and turn it into task board items (backend, frontend, data) with links to the frames, states and the data each element needs. Use when the tech lead shares a Figma link and says "create tasks from this design", "break this page into tasks", "what work does this design need", or wants to plan a page before the designer's formal handoff.
---

# Figma → tasks (tech lead)

The tech lead (e.g. Alex) plans the work for a design. Figma is read-only here: never edit, comment on or move anything
in the Figma file (`scope-guard`). Design changes go to the designer as a task (`handoff`).

## Steps

1. **Read the design** with the `figma` MCP server (`get_metadata` for the page structure, then
   `get_design_context` / `get_screenshot` for the frames that matter). If it isn't connected, ask
   the user to run `/mcp` → `figma` → Authenticate and stop.
2. **List what the design shows**, frame by frame: the page, its states (default, loading, empty,
   error, no permission), filters, tables/charts and every number or label that comes from data.
3. **Check the board for duplicates** (`task-protocol`: open and recently done tasks, the design task if there is one).
   Link instead of duplicating.
4. **Draft the tasks** with the `task-protocol` template, one per team that has work:
   - **frontend** (e.g. Noor): build the page; Figma links to each frame, states, components (your UI library's names).
   - **backend** (e.g. Alex, or Omar on PRC): the API shape from the "data per element" list:
     fields, units, filters, sorting, periods and time zones, which roles see what.
   - **data** (e.g. Kenji), only when a number has no source yet: its business definition.
   - **design** (e.g. Riya), only for gaps: missing states, placeholder copy, unclear fields.
   Put the parent outcome first when there is more than one task, and link the children to it.
5. **State and labels:** `Inbox`, `source:human`. Triage sets the team and assignee; an approver may set
   the priority directly.
6. **Show the user the full drafts and wait for a yes** before creating anything on the board. Then create
   them and reply with the task IDs.

## Gaps to flag (don't guess)

- A number without a clear definition ("paid so far" vs "still owed").
- Missing frames for the empty, error or no-permission states.
- Lorem ipsum, or copy in a different language from the rest of the UI.
- Elements whose visibility per role isn't stated.
