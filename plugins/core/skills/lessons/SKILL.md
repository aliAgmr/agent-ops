---
name: lessons
description: Record a lesson on the task board when you (the agent) got something wrong — a wrong assumption, a guess presented as fact, a claim you didn't check, a rule you missed — and a person had to correct you or you caught it yourself. Use right after such a correction ("that's wrong", "you assumed", "you didn't check", "again?"), or when you notice your own mistake. The triage agent turns a mistake that happens twice into a fix (a guard rule, a skill rule or a test), so it stops recurring.
---

# Lessons: mistakes that should happen once

Writing "I'll remember" does nothing: agents don't remember between sessions. A lesson is recorded
on the task board so the team can count it. When the same kind of mistake happens a second time, triage opens
a task to build a **mechanism** that prevents it: a guard rule, a skill rule, a check or a test.
"Be more careful" is never the fix.

## When to record one
- You stated something as fact that turned out wrong (a column, a route, a number, a file).
- You assumed instead of checking (a definition, which table, what the user meant).
- You missed a team rule (scope, task protocol, API contract, a team convention…).
- You reported something as done or fixed that wasn't.

Not for: typos, a person changing their mind, or normal back-and-forth while designing.

## How
1. Pick a **pattern slug**: kebab-case, about the *kind* of mistake, not this instance
   (`unchecked-column-name`, `paid-vs-remaining`, `claimed-fix-without-test`, `wrong-timezone`).
   First search the task board for existing items labelled `lesson` in this project: if one describes the same
   kind of mistake, **reuse its slug** exactly.
2. Create a work item in the task's project (e.g. BIL, PRC or ANL), state `Inbox`, labels `lesson` and
   `source:agent`, **no assignee**:
   - Title: `Lesson [<slug>]: <one line, what went wrong>`
   - Description:
     ```
     ## What happened
     <what you said or did, and what was actually true>
     ## Why
     <assumed / guessed / didn't check / missed rule X>
     ## Correction
     <what the right answer or rule is; who corrected it>
     ## Where
     <link to the task, MR or file>
     ```
3. Tell your user in one line that you recorded it (`Recorded lesson [<slug>]`). Then carry on with
   the actual work.

Keep it factual and blameless: the point is the pattern, not who made it. Never put secrets, tokens
or personal data in a lesson.
