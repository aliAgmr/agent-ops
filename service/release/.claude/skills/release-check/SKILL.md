---
name: release-check
description: Decide whether a dev→main release merge request may go to production — read the CI E2E report, separate known bugs from new failures, run read-only data checks, post the release-agent commit status, and update the linked board tasks. Use for every open MR (GitLab) or PR (GitHub) into main during a release tick.
---

# Release check for one MR/PR into `main`

"MR" below means a GitLab merge request or a GitHub pull request; use the code host's MCP server
(`gitlab` or `github`) for the repo's host in routing.yaml.

## 1. Collect

- MR: iid, head SHA, the commits/MRs it brings from `dev`.
- The head pipeline and its jobs. A repo with E2E tests has an `e2e` job on MRs into `main`
  (`allow_failure: true`, artifacts in `e2e-report/`). A repo without one is checked on its
  lint/build jobs only (say so in the comment).
- Linked board items: items in `Ready for E2E` whose comments or description link one of the MRs
  merged to `dev` that this release contains.

## 2. Backend E2E (from CI artifacts, don't rerun locally)

Read `e2e-report/summary.md` of the `e2e` job (a GitLab job artifact, or a GitHub Actions artifact). It groups failures by
known bug (`BUG-0xx`, listed in `tests/e2e/BUGS.md`) and lists **unattributed failures first**.

- **Unattributed failures > 0 → RED.** These are new failures. List each: suite, test name, first error line.
- Known-bug failures only → not a blocker by themselves, but compare with the previous release
  report: a known bug that **grew** (more failing tests than last time) → RED.
- A wave of 500s / connection errors from a data source (a database unreachable) → **not a verdict**. Post
  status `pending` with "E2E infra error, rerun needed" and ask the maintainer in the MR to retry the job (you
  never retry it yourself).
- The `e2e` job missing, cancelled or failed before producing a report → `pending`, same message.

## 3. Data checks (read-only)

Run every `checks/*.sql` through the `clickhouse` (or `postgres`, as the file header says) MCP.
Each file states its pass condition in its header comment. Any failed check → RED, with the
numbers. Only SELECT queries; the guard blocks anything else.

A check that couldn't run (query error, timeout, database unreachable, empty answer where rows
were expected) is **unreadable, not passed**: the verdict is at best `pending` with
`check unreadable: <file>`, never `success`. List it in the data checks table as `UNREADABLE`.

## 3a. What the release reaches, and the data traps on it

With the MR's changed file paths (GitLab `list_merge_request_changed_files`; GitHub the PR's files), run both (one plain command
each, the paths as arguments):

```
/srv/agent-ops/infra/bin/lineage files <path> <path> … --impact
/srv/agent-ops/infra/bin/traps for-files <path> <path> …
```

- `lineage` prints the pages, routes, views and metrics downstream of the changed files. Put the
  pages and metrics in the MR comment ("Reaches: …") so the maintainer knows what to look at after the merge.
- `traps` runs the data-trap checks (`plugins/core/skills/data-traps/checks.yaml`) on every table those files read or feed and prints JSON:
  - `blocking` not empty → **RED**, with each check's id, value and `why`;
  - `notify` not empty → a warning line in the comment, no change to the verdict;
  - `unreadable` not empty → at best `pending` with `check unreadable: <id>` (as in step 3), never `success`.
- No changed file reaches a warehouse table (frontend-only MRs) → say "no warehouse tables reached".

## 3b. Consumed contracts (warning only)

If the repo has `docs/CONSUMED_CONTRACTS.md` (a list of outside dependencies the code reads), read it from the MR's head commit.
For each row whose **Used by** files are changed by this release (the MR diff), or whose provider
(view name, `OIDC`, `LDAP`) appears in the diff, check **Last verified**:
- `—` (never verified) or older than 30 days → a **warning** in the MR comment:
  `consumed contract <provider>: last verified <date | never>; code that reads it changed in this release`.
  This doesn't change the verdict. It tells the maintainer which outside dependency to re-check before merging.
- The release's E2E failures pointing at that provider (e.g. a 500 from that view) are still judged in
  step 2 as usual.

## 4. Verdict and status

Post exactly one status on the MR head SHA with `post-commit-status.sh` (with `lineage files` and `traps for-files` from step 3a, the only Bash commands you may run):

```
/srv/agent-ops/infra/bin/post-commit-status.sh <repo-path> <sha> <success|failed|pending> "<one-line reason>" <target-url>
```

`target-url` = the e2e job URL (or the pipeline URL).

## 5. Report

- MR comment (`[agent:release]`): verdict, E2E counts (passed / known-bug / unattributed), data
  checks table, what it reaches and the trap checks (step 3a), consumed-contract warnings (step 3b), links. For RED, the list of failures and which board item each most likely belongs to.
- Linked board items:
  - GREEN → label `e2e:green` (remove `e2e:red`), comment the MR link. They move to `Done` when the maintainer merges.
  - RED → label `e2e:red`, move the item(s) the failures belong to back to `In Progress`, comment
    the failures. If you can't tell which item, comment on all of them and mention the question owner.
- Notifications go out from the board through n8n; you don't message people directly.
