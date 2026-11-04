---
name: write-task
description: Turn a product idea, a stakeholder request, a meeting note or a bug report into a well-formed task board item (or an epic split into tasks) with a clear goal and testable acceptance criteria. Use when product (or anyone acting as product) says "create a task", "add this to the pipeline", "write a ticket/PRD/story", or pastes a request from a business user.
---

# Write a task (product)

A good task lets the triage agent route it without asking, and lets every team know when they are done.

## Steps

1. **Clarify before writing.** If any of these are missing, ask the user (max 3 questions at once):
   - Who is it for (role: finance manager, account manager, executive, support agent…)?
   - What decision or action will they take with it?
   - What does "done" look like on the screen, in numbers?
   - Deadline or reason it is urgent?
2. **Check for duplicates** on the board (`task-protocol`: open and recently done). Link related tasks.
3. **Size it.** If it needs more than one team or more than ~1 week, make a parent task (the
   outcome) and leave the split to triage, or write child tasks yourself when the split is obvious.
4. **Write it** using the template from `task-protocol`:
   - Title: outcome, imperative. "Show overdue amount per customer on the invoices page".
   - Goal: who + what decision + why now.
   - Context/links: screenshots, BI questions, the business rule in plain words, examples with
     real numbers.
   - Acceptance criteria: observable and testable. Name the role that sees it, the page, the
     field, the filter, the edge cases (empty month, no data, negative values), dates and time zones.
5. **State, labels:** `Inbox`, `source:human`. Don't set the team: triage does.
   An approver may set the priority directly (triage keeps it).
6. Show the user the final text before creating it.

## Acceptance-criteria checklist

- [ ] Each line can be checked by someone who wasn't in the meeting.
- [ ] Numbers say which definition (e.g. "MRR as in the metrics spec", "amount still owed, not paid so far").
- [ ] Says which roles can see it and which can't.
- [ ] Says what happens with no data.
- [ ] Dates say which calendar, period and time zone the UI uses.
