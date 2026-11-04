---
name: devops-workflow
description: How a DevOps task goes from the task board to stage — CI/CD config and infra changes as merge requests to dev, running and retrying dev/stage pipelines, GitHub Actions workflows and Jenkins jobs, what counts as production and is off-limits, servers/containers, monitoring and alerts, handoffs and review. Use when starting, finishing or shipping any DevOps task, when asked to run, retry or debug a pipeline, workflow or CI job, or when a task touches .gitlab-ci.yml, .github/workflows, Jenkinsfiles, Dockerfiles, compose files, deploy scripts, servers or alerts.
---

# DevOps workflow

DevOps (e.g. Lena, `team:devops`) owns CI/CD pipelines, deployment setup, servers and containers,
and monitoring and alerts, on every project that has a devops team in `routing.yaml` (in the
examples: **BIL** Acme Billing and **PRC** Acme Pricing, keys like `BIL-12`). Other teams own the
application code in the same repos.

Repos come from `routing.yaml` (each with its code host and default branch), e.g.:

| Project | Repo | Default branch |
|---|---|---|
| BIL | `acme/billing-backend` | `dev` |
| BIL | `acme/billing-frontend` | `dev` |
| PRC | `acme/pricing` | `dev` |
| PRC | `acme/pricing-admin` | `main` (no dev → main release flow) |

## What counts as production (off-limits for the agent)

- **Merge to `main` = automatic production deploy.** Only a maintainer (routing.yaml
  `merge_to_main`) merges to `main`. Never merge any merge/pull request (not even to `dev`: a
  maintainer merges after review).
- Anything whose job, pipeline, workflow, ref, environment or parameters name `prod`, `prd`,
  `production`, `release`, `main` or `master`. On a repo whose default branch is `main`, every
  default-branch pipeline is production.
- Deployment approvals and environments: approve/create/update/delete deployment, stop/delete/update
  environment. Production servers, production containers, production secrets and production alert
  routing: change them only through a reviewed MR that a person applies.
- **Databases:** a "stage" job that migrates or writes data may still touch production data if
  stage and prod share infrastructure. Never run a job that writes to a database; ask a maintainer.

The `core` guard enforces this when your core `role` is `devops`: it allows Jenkins
trigger/rebuild/replay, GitLab create/trigger/retry/play pipeline calls and GitHub run/rerun
workflow calls only when nothing in the call names production. Merges, deployment approvals,
environment changes, push to `main`, force-push, SQL writes and task board deletes stay blocked. If
the guard blocks you, say so and stop; never rename, rephrase or split a call to get around it.

## Running pipelines, workflows and CI jobs (dev/stage only)

1. Know what you're running: read the pipeline/workflow/job first (GitLab `get_pipeline`,
   `get_pipeline_job`; GitHub `get_workflow_run`; the Jenkins job config/last build log). Check its
   ref and environment.
2. **Prefer a fresh run on a branch** (GitLab `create_pipeline` with `ref: dev` or your feature
   branch; GitHub `run_workflow` with `ref`): the ref is in the call, so the guard can check it.
3. **Play/retry/rerun by id** (`play_pipeline_job`, `retry_pipeline`, `rerun_workflow_run`,
   `rerun_failed_jobs`): these calls carry only ids, so add the ref you read in step 1 as
   `"ref": "<branch>"` to the call. Without a ref the guard blocks it. Never put a ref you didn't read.
4. Jenkins: `triggerBuild`/`rebuildBuild`/`replayBuild` on dev/stage jobs only, with the job's full
   name and parameters in the call. Jenkins over `curl` stays blocked for everyone; use the MCP server.
5. Watch it to the end and post the result on the task: link, status, duration, what failed and why.
6. A run that is still waiting for a manual production step stays waiting: tell a maintainer.

## Changing CI/CD and infra config

- One branch per task from `dev` (`ci/<short-name>`, `infra/<short-name>` or `fix/<short-name>`),
  merge/pull request back to `dev`. On a repo without a `dev` branch, open it to `main` and say in it
  that merging deploys to production.
- Pipeline config (`.gitlab-ci.yml`, `.github/workflows/*`, Jenkinsfiles, job definitions),
  Dockerfiles, compose files, deploy scripts and alert rules ship **as files in an MR**. Never edit
  a CI job, a server, a container or an alert live from the agent; if a live change is needed, write
  the exact steps in the task and ask a maintainer (or do it by hand on non-production machines).
- Validate before the MR: CI lint (GitLab's CI lint API, `actionlint` for GitHub workflows),
  `docker build` locally, a dry run of scripts, and a run on your branch (step 2 above).
- The MR description says: what changed, which pipelines/jobs it affects, how it was tested
  (pipeline links), rollback steps, and the task link.
- Never put secrets in files or MR text. Name the CI/CD variable or credential instead and ask a
  maintainer to set it.

## Servers, containers, monitoring and alerts

- Read-only first: logs, `docker ps`/`docker logs`, metrics and dashboards. Anything that restarts,
  scales, or deletes on a shared or production machine is done by a person; write the exact command
  in the task.
- New or changed alerts: put the rule (threshold, window, who gets paged) in the MR and in the task.
  Say which project and environment it covers.
- An outage or a failing production pipeline: open a task right away (`Urgent`), link the evidence,
  and tell a maintainer; don't try to fix production yourself.

## Handoffs

DevOps doesn't change application code. A failing test, a code bug, a missing migration or a
schema question goes to its owner through `core:handoff` (backend, frontend or data, per
`routing.yaml`). Others hand you pipeline, deploy, server and alert work the same way.

## Task protocol and review

Follow `core:task-protocol`:

1. Task is assigned to your human and in `Todo` (see `core:scope-guard`). Move it to `In Progress`
   with a short plan comment.
2. Do the work above. Comment on the task with every pipeline/job you ran (link + result).
3. Open the MR to `dev` and link it on the task.
4. Move the task to **`In Review`** with the done comment (what changed, MR, pipeline links, what's
   left for a person), then stop. An approver reviews it; a maintainer merges. Never move a task
   past `In Review` yourself.
