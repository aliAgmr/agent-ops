---
name: safe-change
description: The guardrails for changing a vibe-coded codebase — the smallest complete change, every changed line traceable to the task, no drive-by refactors, never loosen a check, never touch secrets, careful database migrations, extra care on money, permission and personal-data paths, and reuse of existing components. Use before and while editing any file in a repo of a project without teams, and whenever a change grows beyond what the task asked.
---

# Safe change

Vibe-coded code works until a careless change breaks something nobody remembers. These rules keep
each change small, explainable and reversible.

## Scope
- **Every changed line traces to the task.** If you can't say which part of the task a line serves,
  don't change it. Spotted something else (a bug, ugly code, dead code)? Mention it in your summary
  or a task comment; don't fix it in this change.
- **The smallest complete change:** solve the whole task (callers, tests, types, copy), nothing more.
  No refactors, renames, reformatting or dependency bumps "while you're at it".
- **Reuse before you add.** Follow the existing patterns of the code you are in (its routes,
  services and data access; the module doc the repo's rules file points to). In a UI, use the
  repo's shared components and formatting helpers; never a hand-made variant of a component that
  exists (look for a components folder or a component catalogue in the docs first).

## Never
- **Loosen a check to make it pass:** tests, lint rules, type settings, CI, the project's gates,
  `|| true`, skipped tests. The guard asks the person before any such file changes; say why it is
  needed or don't do it (`scope-guard` rule 10).
- **Touch secrets:** `.env` files, keys, tokens, deploy keys. Use `.env.example` for new settings
  and tell the person what to set where. The guard blocks secrets in anything you write.
- **Run anything against production** (the live API, the live app, production databases or
  servers). Work locally; the MR is the only path to production.
- **Push to `main` or merge** (the guard blocks it; a person merges).

## Database
- Migrations only **add**: new tables, new nullable columns, new indexes. Never drop, rename or
  change the type of something the running version reads (expand → migrate readers → contract in a
  later release).
- Run the migration on your local database, then run the tests twice (a test that only passes once
  hides state left behind).
- Seed data changes need the person's OK.

## Money, permissions and personal data
When a change touches payments, balances, who may do what, or personal data (IDs, bank details,
addresses):
- say so in the plan, before coding;
- keep every permission and role check (who can approve, pay out or see what); a route without its
  check is the worst possible bug;
- never put user input into a query text (use the query builder or parameters);
- never log or return personal data or secrets;
- run the repo's tests for those paths (acceptance or integration tests, when it has them), and ask
  the person for a second review.

## When the change grows
If the task turns out bigger than its brief (more files, a schema change, the other repo), stop and
tell the person: what you found, the options, and your recommendation. Splitting a task is better
than one MR nobody can review.
