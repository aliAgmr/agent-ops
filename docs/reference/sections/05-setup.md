# Setup

The whole setup is one file: **`SETUP.md`** at the repository root. Open this repository in your AI
tool (Claude Code, Cursor, Codex or Kimi) on the machine that will be the agent host, and say
"follow SETUP.md". It:

1. **Asks every question first**, in one go: your team (generic keys, roles, emails, time zones),
   which AI tool each person uses, your task board and code hosts, your projects and repos, the host's
   operating system, which service agents to run, their schedules and models, the budget, and which
   optional parts you want (notifications, desks, a self-hosted Plane, data sources).
2. **Writes your configuration**: `routing.yaml`, `projects.yaml`, `people.yaml` (git-ignored),
   `knowledge/lineage.yaml`, the env file skeleton.
3. **Has you enter every secret yourself**, with hidden input in your own terminal, never in chat:
   API keys, bot tokens, read-only database URLs, revert tokens.
4. **Installs and starts** the host side (launchd or systemd), the task board's states and labels,
   the read-only repo clones, agent-mcp and the wake listener.
5. **Verifies each part** as it goes (a dry run of every service agent, the guard tests, agent-mcp's
   health, a journal round trip, a lineage build) and stops with a clear message when something
   fails.
6. **Generates one onboarding prompt per person** for their client (Claude Code plugin install, or
   `agent-ops setup --client cursor|codex|kimi`).

Re-running it is safe: it reads what exists and only asks about what's missing or what you want to
change.

## What you need before you start

- A machine that can stay on: macOS 13+ or a Linux server (Debian/Ubuntu tested), with admin
  rights.
- A Claude API key (recommended for unattended runs) or a Claude subscription token.
- A task board (Plane Cloud, or SETUP.md installs Plane Community Edition for you) and an admin
  token for it.
- Your code host(s): GitLab or GitHub, and the right to create bot accounts or tokens.
- Optional: read-only users for your databases and warehouse, a Telegram bot, a Figma token.
