---
name: approve
description: Review what the triage agent sorted (optional: correct team, assignee or priority), approve or send back tasks in In Review, and answer the triage agent's open questions. Use when an approver asks "what did triage sort", "review triage", "what needs my review", "approve BIL-12", or "answer the questions".
---

# Review triage and approve reviews

There is no approval gate after triage: the triage agent assigns each task, sets a default priority
and moves it straight to `Todo`, where work can start. Reviewing what triage sorted is optional; do
it when an approver (routing.yaml `approvers`, e.g. Sam and Alex) wants to correct something.

## Steps

1. List items triage moved to `Todo` recently (its `[agent:triage] routed to …` comment), newest
   first, not yet `In Progress`.
2. For each one, show: title, assignee/team, priority (triage notes say `triage default` unless an
   approver set it), the **Triage notes** (teams + order, what is touched, risks, estimate) and any
   child tasks it created.
3. Ask per task: **keep**, **change** (team / assignee / priority / split), or **reject**.
   - keep → nothing to do (optionally comment `[agent:<name>-product] triage checked`).
   - change → edit the fields; comment what changed and why (priority: `[agent:<name>-product]
     priority medium → high: …`, as in `prioritise`).
   - reject → move to `Cancelled` (approvers only) with the reason, or back to `Inbox` with a
     comment saying what's missing.
4. Then list open `question` items assigned to the question owner (or to product, if it's a product
   question). Help write the answer as a comment, and close the question item when it's answered.
5. Then list items in `In Review` (waiting for an approver's review), oldest first, with the done
   comment and its links. Per item the approver says **approve** (merge requests merged to `dev`, on
   stage → move to `Ready for E2E`, comment `[agent:<name>-product] reviewed, approved`) or
   **changes needed** (move back to `In Progress` with what to change). Only approvers make this
   move; the triage agent reverts it when anyone else does.

Never change, reject or approve on the person's behalf without their explicit "yes" per task, or
per batch if they say so.
