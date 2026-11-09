---
name: backend-workflow
description: How a backend task goes from the task board to stage — branches, merge requests to dev, migrations, warehouse DDL handover, CI job changes, E2E, and closing the task. Use when starting, finishing or shipping any backend task, or when asked how backend changes reach stage or production.
---

# Backend workflow

The example below assumes a `dev` branch that deploys to stage and a `main` branch that deploys to
production. Adjust the branch names and deploy steps to your repos; keep the rules.

## Branches and environments

- `dev` is the default and working branch. **Merge to `dev` = deploy to stage.**
- **Merge to `main` = automatic production deploy.** Only a maintainer (routing.yaml `merge_to_main`) merges
  to `main`, and only after the release agent's E2E check is green. Agents never merge to `main` (the guard blocks it).
- One branch per task: `feature/<short-name>` or `fix/<short-name>`, from `dev`, merge/pull request
  back to `dev`.
- Treat every database as production: stage and prod often share infrastructure.

## Steps for a task

1. Task is assigned to your human, in `Todo` (see `scope-guard`). Move to `In Progress`.
2. Branch from `dev`. Implement, following the repo's conventions for errors, enums, fields and
   user-facing text (put team-specific ones in a skill of your own).
3. Tests: follow `e2e-tests` (a separate agent writes the E2E tests; run the related suites). Fix the
   code, not the check: edits to `BUGS.md`, CI, lint or type config ask your person first (`scope-guard` rule 10).
4. **Database migrations** ship in the MR; the deploy runs them before the new version starts
   serving, so a migration runs **while the old version is still serving**.
   Never drop, rename or retype something the running version reads. Use **expand → contract**:
   1. **Expand:** add the new column or table (nullable, or with a default) and deploy. The old code
      is unaffected.
   2. **Backfill:** fill it in the same or a later migration, or a job, and **switch readers** to it in
      code.
   3. **Contract:** in a *later* release, once no deployed version reads the old column or table,
      drop it.

   A CI job can warn about DROP / RENAME / type change / NOT NULL / TRUNCATE /
   DELETE in new migrations. When one is intended and safe, add `-- migration-safe: <why>` to that
   `migration.sql`, e.g. "no reader since abc1234".
5. **Warehouse DDL** (views, tables) is never run by an agent: put the final SQL (in
   `clickhouse-query-style` or your warehouse's style) in the MR description and the task, and ask a
   maintainer to apply it.
6. **CI jobs:** change job definitions as files in a MR; never trigger or edit jobs live.
7. Open the MR to `dev` with: what changed, how it was tested (E2E counts), DDL to apply, the task link,
   and the handoff doc (step 9).
8. **Docs and outside dependencies:** keep the docs that describe the code you changed current in
   the same MR. If the repo keeps a list of outside dependencies (e.g. `docs/CONSUMED_CONTRACTS.md`:
   warehouse views, identity providers), update it, and set **Last verified** when you check one
   against stage.
9. **API handoff (required when the API changed):** follow `api-contract`: write or update
   `docs/frontend-handoff/<feature>.md` in this MR, update the related frontend task with the
   contract and the doc link (or create it), and post the contract on this task. No API change:
   say so in the done comment.
10. Move the task to `In Review` with the done comment, then stop. An approver reviews it; a maintainer
   merges to `dev`, and an approver moves the task to `Ready for E2E` when it is on stage. Never
   move it past `In Review` yourself (triage reverts it).
