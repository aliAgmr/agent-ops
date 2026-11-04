# SYNC: how this repo maps to a private deployment

agent-ops started as one team's private repo and is maintained in both places. Changes are ported
**by hand, both ways**, each as its own branch and review:
- this repo gets a GitHub pull request;
- the private one gets a merge request in its own forge.

When porting, rewrite the change generically:
- names and hosts become the example team and `*.example.com`;
- direct API calls become the adapters;
- env names follow the table below.

Before opening the PR, run `bash tools/lint.sh` and `bash tools/run-tests.sh` here.

If you run your own fork, you can keep a file like this to track what you changed.

## Same path, same purpose (port the change as is, made generic)

| Path | Notes |
|---|---|
| `plugins/core/**` | Guard, journal hook, session notes and core skills. Skill text is generic here. |
| `plugins/role-*/skills/**` | Role skills. Here role plugins carry skills only (see below). |
| `service/{triage,analyst,release,nightly,agent-mcp}/**` | Prompts, ticks, skills, settings. Here the MCP configs are rendered from `mcp.template.json`. |
| `infra/bin/{run-agent.sh,alert.sh,repo-sync.sh,nightly.sh,nightly-send.sh,journal,lineage,traps,figma-shot.py}` | Host scripts. |
| `infra/agent-mcp/**`, `infra/journal/**`, `infra/quality/**`, `infra/nightly/**` | Server, journal, quality and nightly. |
| `infra/macos/**`, `infra/systemd/**`, `infra/bootstrap.sh` | Host install. Here the launchd prefix is `io.agent-ops` and Linux is at parity. |
| `infra/plane/**`, `infra/n8n/**` | Task-board install/configure, and notifications. |
| `clients/**` | Cursor/Codex installer. Here it also has the Kimi client and `--tools`. |
| `desks/{CLAUDE.md,SETUP.md,setup.sh,templates,claude-memory,.githooks}` | The desk framework. |
| `docs/reference/{build.py,page.html}` | The reference generator. |
| `tests/*` | Same suites. Here the fixtures use the example team. |

## Same purpose, different shape

| Here | Private side | How to port |
|---|---|---|
| `infra/adapters/**` (tracker: Plane; code host: GitLab, GitHub) + `infra/bin/adapter` | Direct Plane/GitLab calls inside scripts | Port behaviour into the adapter method, then call it |
| `infra/bin/precheck.py` (+ `precheck.sh` wrapper) | `infra/bin/precheck.sh` (bash + curl) | Same rules; port the rule, not the curl |
| `infra/bin/post-commit-status.py` | `infra/bin/post-commit-status.sh` | Through the code-host adapter |
| `infra/bin/render-mcp.py`, `service/*/mcp.template.json` | Committed `service/*/.mcp.json` | Edit the template, not a rendered file |
| `plugins/{gitlab,github,postgres,clickhouse,jenkins,figma,metabase}-tools` | MCP servers inside the role plugins | Server changes go to the tool plugin |
| `plugins/core/skills/data-traps/` (example checks) | A warehouse-traps skill with real checks | Port the format and runner changes, never the checks |
| `infra/lineage/scanners` + `knowledge/lineage-sources.yaml` | Scanners wired to one stack | Port scanner fixes into the matching built-in scanner |
| `desks/{bi-manager,ci-manager,debugging}` (examples) | The team's own desks | Port only framework changes |
| `docs/reference/sections/01…08` (generic) | The team's reference sections | Port the generic part of a docs change |
| `README.md`, `SETUP.md`, `CONTRIBUTING.md`, `AGENTS.md`, `LICENSE`, `.github/`, `tools/` | — | Exist only here |
| — | People, routing and projects of the real team, onboarding prompts, private syncs, private desks | Never ported |

## Environment names

| Here | Private side (typical) |
|---|---|
| `TRACKER_BASE_URL`, `TRACKER_WORKSPACE` | `PLANE_BASE_URL`, `PLANE_WORKSPACE_SLUG` |
| `TRACKER_TRIAGE_TOKEN`, `TRACKER_RELEASE_TOKEN`, `TRACKER_NIGHTLY_TOKEN` | `PLANE_TRIAGE_TOKEN`, `PLANE_RELEASE_TOKEN`, `PLANE_NIGHTLY_TOKEN` |
| `CODEHOST_<KEY>_TOKEN`, `CODEHOST_<KEY>_RELEASE_TOKEN` | `GITLAB_AGENT_TOKEN`, `GITLAB_RELEASE_TOKEN` (one host per variable) |
| `revert.env`: `TRACKER_REVERT_TOKEN`, `CODEHOST_<KEY>_REVERT_TOKEN` | `revert.env`: `PLANE_REVERT_TOKEN`, `GITLAB_REVERT_TOKEN` |
| `JENKINS_MCP_URL`, `AGENT_ALERTS` | `JENKINS_URL`-style, `ALERTS` |
| Core plugin options `tracker_api_key`, `tracker_base_url`, `tracker_workspace_slug`, `agent_mcp_url`, `role` | `plane_api_key`, `plane_base_url`, `plane_workspace_slug`, `role` (agent-mcp URL fixed) |
| `routing.yaml` repos `host: <code_hosts key>` | `gitlab: main|data` |
| `projects.yaml` `tracker_project_id` | `plane_project_id` |
