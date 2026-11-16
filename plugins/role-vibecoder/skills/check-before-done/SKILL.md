---
name: check-before-done
description: How to prove a change really works before calling it done — run the repo's own checks (the project's gates, tests, build and lint), open it locally and look, try the edge cases, take before/after screenshots for UI, run the reviewer agent, and report what was and wasn't checked. Use before every merge request and every "done" on a task of a project without teams, and when the person asks "does it work?".
---

# Check before done

"It should work" is not done. Done means you ran it, looked at it, and can show the results.

## 1. The repo's checks
Use the commands the repo really has: read its rules file, its README, the `scripts` in
`package.json` (or the Makefile, `pyproject.toml`…) and the `scripts/` folder first; they change.
Typically:
- **the project's gates:** the script its CI runs (e.g. `scripts/verify.sh` or `npm run check`);
  run the same one locally;
- **types and lint:** e.g. `npm run typecheck`, `npm run lint`;
- **tests:** e.g. `npm test`, the unit and integration suites; the end-to-end specs that cover the
  screens you changed (they may need the local database or services from a `docker-compose` file);
- **build:** e.g. `npm run build`;
- the repo's full local CI script, when it has one and the change is big.

A failing check: explain it to the person and fix the cause (`safe-change`). A check you couldn't
run (no database, no Docker): say so under "Not checked".

## 2. See it working
- **Pages:** run the app (e.g. `npm run dev`, at the local URL its README gives), open the page and
  do what a user would do. Take a screenshot before and after, at desktop and phone width. Check
  empty and loading states, long text, and the roles that should and shouldn't see it. When the app
  has locale rules (right-to-left layout, local digits, currency and date formats), check those
  too.
- **API changes:** call the endpoint locally (as each relevant role), including a wrong input and a
  caller without permission. Check the response shape against what the frontend expects.

## 3. A second pair of eyes
Run the `reviewer` agent (core plugin) on your diff with the task; fix what it lists under Must fix
(its `findings-format`). Put its verdict line in the done comment.

## 4. Report
In the MR and the done comment (`explain-change`): the commands and their results, what you looked
at, the screenshots, and a **Not checked** line. Never round "not run" up to "passed".
