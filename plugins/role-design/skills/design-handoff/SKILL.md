---
name: design-handoff
description: Hand a finished Figma design to backend and frontend as task board items with everything they need — frames, states, fields, the data each element shows, locale and direction notes. Use when the designer finishes or updates a page design, says "hand off", "send to frontend/backend", "design is ready", or a design task moves to In Review.
---

# Design handoff

Goal: frontend (e.g. Noor) can build the page and backend (e.g. Alex) can build the API without
asking the designer anything.

## Checklist (fill every line, or write "n/a")

**Frames**
- [ ] Figma link to the page frame (and to the prototype flow if there is one).
- [ ] Every state has its own frame: default, loading, empty (no data), error, no permission, long text.
- [ ] Responsive: which breakpoints exist (desktop only is OK, say so).

**Components**
- [ ] Which existing components are reused (name the UI library's component when it's a standard one: Table, Select, DatePicker, Modal…).
- [ ] New components: their variants and states.

**Data per element** (this becomes the API contract; be exact)
- [ ] For every number, label and column: what it shows in business words ("amount still owed, direct sales only").
- [ ] Units and format: currency and its scale (e.g. thousands "K"), %, decimals, negative style.
- [ ] Filters and sorting: which fields, default values, multi-select or single.
- [ ] Date pickers: calendar, month or day, default period, time zone.
- [ ] Which roles see the page and which elements are hidden per role.

**Language and direction**
- [ ] The UI language(s) and direction (left-to-right or right-to-left). For RTL, mirror icons and charts that read left-to-right where needed.
- [ ] Final copy is in the frame (not lorem ipsum). Mark text the backend returns (translated server-side) vs static UI text.

## Steps

1. Run through the checklist with the designer using the Figma MCP to read the frames; list the gaps.
2. Move the design task to `In Review` with the done comment (`task-protocol`). An approver
   reviews it and moves it on; don't move it past `In Review` yourself.
3. Use the `handoff` skill to create:
   - **frontend task:** build the page; link the frames, list states and components.
   - **backend task** (if new or changed data): the "Data per element" list as the expected API shape.
   - **data task** (only if a number has no source yet): the business definition of the missing number.
   Link all of them as children of the original product task.
