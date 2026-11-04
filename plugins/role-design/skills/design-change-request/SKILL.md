---
name: design-change-request
description: Handle a change to an existing design — requested by product, backend, frontend or data — or propose one. Use when someone asks the designer to change a page, a field doesn't fit the design, the API can't provide what the design shows, or the designer wants to update a built page.
---

# Design change request

## Incoming (someone asks design for a change)

1. Read the task: what changes, why, who asked (product decision? technical limit from backend/data/frontend?).
2. If it's a technical limit ("this number doesn't exist per day, only per month"), adapt the
   design to what's possible and say what changed. If it changes what users can decide, ask product
   (e.g. Sam) first.
3. Update Figma. In the frame, mark changed elements (a "changed" annotation with the date).
4. Comment on the task: before/after frame links and the list of changed elements.
5. Hand off to whoever must rebuild it (frontend, and backend if the data per element changed),
   using the `design-handoff` checklist for only the changed parts.

## Outgoing (design wants to change a built page)

1. Create a task in `Inbox`: the reason (usability problem, consistency, new requirement), the
   before/after frames, and the affected roles.
2. Triage routes it and puts it in `Todo`. Don't hand it to frontend directly.
