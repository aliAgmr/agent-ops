# Setting up the desks for yourself

This folder (`desks/` in the agent-ops repo) is a team template for a set of Claude "desk" sessions:
long-running sessions in the Claude desktop app, each owning one area. The examples here own the BI
tool, CI jobs and local app debugging. Anyone on the team can clone it, and the desks then work for
that person ("the owner").

What is shared and what is yours:
- **Shared (in git):** `CLAUDE.md` (rules for all desks), each desk's `DESK.md` (what it does and how),
  `setup.sh`, `templates/`, `claude-memory/`.
- **Yours (never committed):** `workspace.local.md` (your name and paths), each desk's `STATE.md`
  (your open items and history), `.claude/launch.json`, and anything else a desk saves in its folder.

## 1. Clone
Clone your team's agent-ops repo, ideally next to your other repos:
```bash
git clone <your agent-ops repo URL>
```
The desks are its `desks/` folder. This clone is only for the desks; your Claude Code plugins still
come from the `agent-ops` marketplace.

## 2. Run setup
```bash
cd agent-ops/desks && ./setup.sh
```
It asks for your name, where your backend, frontend and data repo checkouts are, and the commands
that start the backend and frontend in dev mode (Enter keeps the default). Then it:
- writes `workspace.local.md` and `.claude/launch.json` for you
- creates an empty `STATE.md` per desk
- turns on the commit check that refuses secrets (`core.hooksPath desks/.githooks`, for the whole clone)
- copies the shared notes in `claude-memory/` into this folder's Claude memory
- prints **ok / WARN** checks for what the desks need

It never overwrites your `STATE.md` files, so you can re-run it any time. `./setup.sh --force` rewrites
`workspace.local.md` and `launch.json`.

## 3. What each desk needs
Each desk's `DESK.md` has a "Setup for a new owner" section with the details. In short:

| Desk | Needs |
|---|---|
| BI Manager | the `metabase-tools` plugin (or your BI tool's MCP); your own BI login; the BI tool reachable in the built-in browser for writes |
| CI Manager | the `jenkins-tools` plugin (or your CI's MCP); your own CI user and API token in `~/.ci-basic` (chmod 600); `~/ci-backups/` |
| Backend/Frontend Debugging | backend and frontend checkouts; their runtimes (e.g. Node); access to a dev/stage database (VPN or tunnel); the backend's local env file |

You only need what's required by the desks you'll use. Start the others later, or not at all.

## 4. Create the "Agents" preset
In the Claude desktop app, create a local session preset named **Agents** with this folder
(`agent-ops/desks`, not the repo root).

## 5. Start the desks
Open a session in this folder and say **"Start the desks from SETUP.md"** (or name the ones you want).
Claude starts one session per desk with the Agents preset, the title below and this prompt:

| Title | Desk folder |
|---|---|
| Agent \| BI Manager | `bi-manager/` |
| Agent \| CI Manager | `ci-manager/` |
| Agent \| Backend/Frontend Debugging | `debugging/` |

> You are the owner's "<title>" desk in the Agents workspace. Read `CLAUDE.md`, `workspace.local.md`,
> `<desk folder>/DESK.md` and `<desk folder>/STATE.md` before anything else, and take over the desk.
> In this first turn stay read-only: don't edit repos, start servers, or write to any shared system.
> Check that the tools and access in DESK.md's "Setup for a new owner" work, then reply to the owner
> with a short status (what works, what's missing, what's open) and wait for direction. From now on
> keep `<desk folder>/STATE.md` current.

## 6. Adding a desk
1. Create `<name>/DESK.md` with the same sections as the examples: Role, Standing rules, Scope and
   requests, how it works (tools, commands, gotchas), Setup for a new owner (each need with a check
   command).
2. Add it to the tables in `CLAUDE.md` (Desks, Scope) and in this file (step 3 and 5).
3. Ship it as a structure change (`CLAUDE.md`, "Structure changes always go to the repo").

## 7. Staying up to date
Improvements to the desks reach everyone through agent-ops' `main`. Pull now and then (`git pull --ff-only`);
your `STATE.md` files are untouched by pulls. When you or a desk improves how a desk works, it goes
back as a merge request (see "Structure changes always go to the repo" in `CLAUDE.md`).

Your `STATE.md` files live only on your machine. To move them to another machine or Claude account,
copy the desk folders yourself.
