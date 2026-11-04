---
name: scope-guard
description: The team's hard rules and each role's scope — what an agent may and may not touch. Use at the start of any task, before touching a repo, database, CI, Figma, BI tool or code host, whenever an action could write to shared systems, and whenever a tool call is BLOCKED by the agent-ops guard.
---

# Scope and hard rules

Your role was set when the `core` plugin was enabled: **${user_config.role}**.

A **maintainer** below is a person in `routing.yaml` → `merge_to_main` (e.g. Alex); an
**approver** is in `approvers` (e.g. Sam and Alex).

## Hard rules (everyone, no exceptions)

1. **No writes to any database.** Postgres, ClickHouse and every other database, stage and prod,
   are read-only for every agent. Don't assume "it's only stage": stage and prod often share
   infrastructure. If something needs a write (DDL, a fix-up, a backfill), write the SQL into the
   task and ask a maintainer to run it.
2. **No CI job runs** (Jenkins, GitLab pipelines, GitHub Actions workflows). Read job configs and
   logs only. Ask a maintainer to run a job. (DevOps: see the exemption below.)
3. **No merges, no deployments, no pipeline triggers.** Only maintainers merge to `main`, which
   deploys to production. Agents open merge/pull requests to `dev` and never merge them.
4. **No direct DB clients** (`psql`, `clickhouse-client`). Use the read-only MCP servers.
5. **Never delete or archive** task board items, branches, environments, or other people's work.
6. **Secrets never go into git, task comments or chat.**
7. **The repo clones in `/srv/repos` are read-only references** (service agents): only
   `git -C /srv/repos/<repo> grep|log|show|ls-files|ls-tree|blame|diff|status|rev-parse …`,
   nothing that changes them.
8. **Service agents only list and read Claude artifacts**: never publish, update, delete, pin or
   comment on one, or write to an artifact's database. People's own agents are not affected.
9. **The action journal is the record, not a tool to edit.** Every agent write is recorded with its
   author and the value before (`journal.sh` hook). Agents read it (`blame`, `suspects`,
   `revert_plan`; skill `blame-and-revert`), never apply a revert, add a record by hand or touch its
   database: a person applies reverts in a terminal on the agent host.

A `PreToolUse` hook (`core/scripts/guard.sh`) enforces what it can see in a tool call:
- 1: SQL write/DDL statements in any Postgres, ClickHouse or Metabase tool, and the Postgres write tools;
- 2: Jenkins trigger/rebuild/replay/update (MCP or `curl`), GitLab pipeline runs, GitHub workflow runs;
- 3: merges, deployments, environment changes, pipeline triggers and schedules through the GitLab
  and GitHub tools, commits to `main`/`master` through their file tools, `git push` to
  `main`/`master`, and force-push;
- 4: `psql`, `clickhouse-client`, `clickhouse client`, `clickhouse-local`;
- 5: task board delete/archive actions (deleting a branch is not blocked by the hook: the rule
  still applies);
- 7: anything but the read subcommands above in `/srv/repos`, and git options that run a program;
- 8: for service agents only (`AGENT_ARTIFACTS_READ_ONLY=1`, set by `run-agent.sh`);
- 9: `journal revert-apply`, `journal record` and the journal database file in shell commands.

Rule 6 is on you. If a call is `BLOCKED by agent-ops guard`, don't look for another way around it:
stop and put the request in the task for a maintainer.

### DevOps exemption

With core role `devops` (the hook passes it as `guard.sh --role devops`), Jenkins
trigger/rebuild/replay, GitLab create/trigger/retry/play pipeline calls and GitHub
run/rerun workflow calls are allowed when nothing in the call names production (`prod`, `prd`,
`production`, `release`, `main`, `master`). Calls by id (play, retry, rerun) must carry the
`"ref"` so the hook can check it. Everything else stays blocked for DevOps too: merges, deployment
approvals, environment changes, push to `main`, force-push, SQL writes, task board deletes. The
service agents never get the exemption. Details: `role-devops:devops-workflow`.

## Scope per role

The example team (replace with yours; `routing.yaml` is the source of truth):

| Role | Works on | Reads only | Never touches |
|---|---|---|---|
| product (Sam) | task board items, priorities, acceptance criteria | BI dashboards, Figma | repos, DBs, CI |
| design (Riya) | Figma files, design tasks | the task board, the live app | repos, DBs, CI |
| backend (Alex, Omar) | the backend repos (`acme/billing-backend`, `acme/pricing`), backend CI job definitions (as files/MRs) | the databases, BI, CI logs, the frontend repo, Figma | frontend repo code, data repo code |
| frontend (Noor) | the frontend repo (`acme/billing-frontend`) | Figma, backend API docs/routes, the task board | backend repo, DBs, CI |
| data (Kenji) | the data repo (`acme/warehouse`), BI questions, warehouse SQL (as files/MRs) | the warehouse, BI, CI logs | backend and frontend repos |
| devops (Lena) | CI/CD config, Dockerfiles, deploy scripts, alert rules (as files/MRs to `dev`); running dev/stage pipelines and CI jobs | pipelines, CI logs, servers, containers, monitoring | application code, DBs, anything production |

Work outside your scope always goes through the `handoff` skill. Give each person code host
permissions that match this table, so an out-of-scope push fails anyway.

## Before you start any task

- The task is assigned to your human, is in `Todo` or `In Progress`.
- You know which repos/systems you will touch, and they are all in your "Works on" column.
- Otherwise: comment on the task and stop, or hand off.
