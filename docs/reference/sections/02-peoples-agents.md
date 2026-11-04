# People's agents

Each person keeps using the AI tool they like (Claude Code, Cursor, Codex or Kimi) with their
own accounts. agent-ops gives all of them the same team rules, skills, MCP servers, guard and
journal: as Claude Code plugins, or rendered by the `agent-ops` installer for the other clients.

## The plugin marketplace

`.claude-plugin/marketplace.json` lists the plugins under `plugins/`:

| Plugin | For | Brings |
|---|---|---|
| `core` | everyone | skills `task-protocol`, `scope-guard`, `handoff`, `ask-project`, `message-teammate`, `lessons`, `directives`, `blame-and-revert`, `data-traps` (with executable checks), `clickhouse-query-style`; the task-board MCP server and the agent-mcp client; hooks: guard (`PreToolUse`), journal (`PreToolUse` + `PostToolUse`), session notes (`SessionStart`); the `reviewer` subagent |
| `role-product` | product | skills `write-task`, `prioritise`, `approve` |
| `role-design` | design | skills `design-handoff`, `design-change-request` |
| `role-backend` | backend | skills `backend-workflow`, `api-contract`, `e2e-tests`, `figma-to-tasks` |
| `role-frontend` | frontend | skill `fe-integration` |
| `role-data` | data | skill `data-delivery` |
| `role-devops` | devops | skill `devops-workflow` |
| `gitlab-tools` | anyone on GitLab | GitLab MCP server (`gitlab_url`, token; read-only unless the role needs writes) |
| `github-tools` | anyone on GitHub | GitHub's hosted MCP server (read-only header unless the role needs writes) |
| `postgres-tools` | backend, data | Postgres MCP server with a read-only connection string |
| `clickhouse-tools` | backend, data | ClickHouse MCP server with a read-only user |
| `jenkins-tools` | backend, data, devops | Jenkins MCP bridge (runs blocked by the guard, DevOps: non-production only) |
| `figma-tools` | product, design, frontend | Figma's MCP server |
| `metabase-tools` | product, backend, data | a vendored Metabase MCP server |

Role plugins carry skills only; MCP servers come from the tool plugins each person installs for the
systems they use, so nobody gets a server (or a token prompt) they don't need. SETUP.md asks which
tools each person uses and gives them the matching install lines.

### Plugin layout

```
plugins/<name>/
  .claude-plugin/plugin.json   name, version, userConfig (settings Claude Code asks for)
  .mcp.json                    MCP servers; secrets as ${user_config.<key>}
  skills/<skill>/SKILL.md      front matter: name, description (when to use it)
  hooks/hooks.json             core only
  scripts/                     core only: guard.sh, journal.sh, session-notes.sh, the Plane CE shim
  agents/                      core only: the reviewer subagent
```

### Settings

`core` asks for `tracker_api_key` (sensitive, stored in the OS keychain), `tracker_base_url`,
`tracker_workspace_slug`, `agent_mcp_url` (your host's public agent-mcp address, e.g.
`https://agents.example.com/agent-mcp`) and `role`. Hooks read them as
`CLAUDE_PLUGIN_OPTION_<KEY>`. Role plugins ask for the person's own read-only database URLs, a
code-host token (`api` scope, Developer role, never Maintainer) and similar. Nothing secret is ever
written to the repo.

### Installing (Claude Code)

```
/plugin marketplace add https://github.com/<you>/agent-ops.git
/plugin install core@agent-ops
/plugin install role-<role>@agent-ops
```

`SETUP.md` generates a ready-to-paste prompt per person that does this, checks each MCP server and
the hooks, and never asks for a secret in chat.

### Versioning and updates

Every change to a plugin bumps its `version` in `plugin.json` in the same commit: Claude Code only
updates a plugin when its version changes. People update with
`claude plugin marketplace update agent-ops` and `claude plugin update <plugin>@agent-ops`.
Never `claude plugin marketplace remove`: it uninstalls the plugins and deletes their settings.
Service agents don't install plugins: they read `plugins/` from the host's checkout.

## The core skills

| Skill | What it does | When |
|---|---|---|
| `task-protocol` | states, labels, the description template, the comment format, who may move what | every task-board action |
| `scope-guard` | the hard rules, which the guard enforces, each role's scope | start of a task; any blocked call |
| `handoff` | opens a task for another team (with a "blocked by" relation) instead of doing their work | work outside your scope |
| `ask-project` | asks agent-mcp about code, data or history you can't see | before guessing |
| `message-teammate` | a private Telegram message from the team bot, signed with the person's name | only when the person asks; shows recipients and text first |
| `lessons` / `directives` | agents record their own mistakes as `lesson` items; a repeat opens a mechanism task; directives are open team rules | after a correction; at session start (the session-notes hook shows recurring ones) |
| `blame-and-revert` | `blame`, `suspects`, `lineage`, `revert_plan` through agent-mcp | who changed it, what broke it, undo it |
| `data-traps` | your team's known data traps, as prose plus executable checks (`checks.yaml`) | before any warehouse query |
| `clickhouse-query-style` | an example house style for warehouse SQL (attribution comment, CTEs) | writing SQL |

The `reviewer` subagent reviews a diff for defects before a task goes to `In Review`.

## Hooks

| Event | Matcher | Command | Timeout |
|---|---|---|---|
| `PreToolUse` | `Bash\|mcp__.*` | `scripts/guard.sh --role "$CLAUDE_PLUGIN_OPTION_ROLE"` | 10 s |
| `PreToolUse` | `Bash\|Artifact.*\|mcp__.*` | `scripts/journal.sh pre` | 8 s |
| `PostToolUse` | `Bash\|Artifact.*\|mcp__.*` | `scripts/journal.sh post` | 8 s |
| `SessionStart` | — | `scripts/session-notes.sh` | 20 s |

- `guard.sh` blocks with exit 2 and a reason. Claude Code treats only exit 2 as a block; the
  Cursor/Codex/Kimi adapter blocks on any failure, so a broken guard never means "anything goes".
- `journal.sh` sends each write-looking tool call to agent-mcp's `/journal` with the person's
  task-board token, before (so the old value is saved) and after. It never blocks or prints, and
  spools to a local file when agent-mcp is unreachable.
- `session-notes.sh` prints recurring lessons and open directives (at most ~15 lines, cached an hour).

## Clients: Cursor, Codex and Kimi (`clients/`)

The plugins in `plugins/` stay the single source of truth; `agent-ops` renders them for each client.

```bash
git clone https://github.com/<you>/agent-ops.git ~/.agent-ops/repo
uv tool install --editable ~/.agent-ops/repo/clients
agent-ops setup --client cursor --role frontend --person noor     # or --client codex / --client kimi
agent-ops check
```

| | Claude Code | Cursor | Codex | Kimi Code |
|---|---|---|---|---|
| Skills | plugins | `~/.agents/skills/` | `~/.agents/skills/` | `~/.agents/skills/` |
| Always-on rules | plugin skills + CLAUDE.md | a local plugin with one `alwaysApply` rule | a marked block in `~/.codex/AGENTS.md` | a marked block in `~/.kimi-code/AGENTS.md` |
| MCP servers | plugin `.mcp.json` | `~/.cursor/mcp.json` | `~/.codex/config.toml` | `~/.kimi-code/mcp.json` |
| Guard hook | plugin hook | `beforeShellExecution`, `beforeMCPExecution` (fail closed) | `PreToolUse` | `PreToolUse` in a managed block of `~/.kimi-code/config.toml` (fails open; backed by `[[permission.rules]]` deny rules) |
| Journal hook | plugin hooks | before/after shell and MCP | `PreToolUse` / `PostToolUse` | `PreToolUse` / `PostToolUse` |
| Session notes | yes | yes | yes | no |
| macOS / Linux / Windows | yes / yes / yes | yes / yes / yes (PowerShell hook commands) | yes / yes / yes | yes / yes / yes (`$KIMI_CODE_HOME`, default `~/.kimi-code`) |

How it works:
- **Secrets** are asked with hidden input and stored in the OS keychain (macOS Keychain, Windows
  Credential Manager, Secret Service on Linux; service `agent-ops`, account `<plugin>.<option>`).
  Non-secret values go to `~/.agent-ops/config.json`.
- **MCP servers** are written with no values at all: each starts as
  `<python> -m agent_ops mcp <plugin>/<server>`, a launcher that reads the plugin's `.mcp.json`,
  fills `${user_config.*}` from config and keychain, and starts the real server with the values in
  its environment. OAuth servers (Figma) are written as plain remote servers.
- **The guard** runs `python -m agent_ops guard --client <client>`: it turns the client's hook input
  into Claude Code's `PreToolUse` shape and runs the unchanged `guard.sh` with the person's role.
  If it can't run, the call is blocked. Windows needs Git for Windows' `bash.exe` (never WSL's).
- **The journal** runs `python -m agent_ops journal --client <client> --phase pre|post` the same way.
- **Only agent-ops' own entries** are added, replaced or removed; a person's other servers, hooks,
  skills and text stay untouched; each file is backed up once. `agent-ops uninstall` removes
  everything it added.
- **Updates:** a session-start hook runs `agent-ops update` at most once an hour; it never blocks a
  session.
- **Codex** runs user hooks only after the person trusts them once with `/hooks`.

`docs/clients.md` has the full support matrix, including what Kimi can and can't do.
