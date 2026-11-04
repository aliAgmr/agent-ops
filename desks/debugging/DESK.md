# Backend/Frontend Debugging desk

Local state (current status, open items, history) lives in `STATE.md` next to this file.

## Role
The owner's standing debugging desk for the team's app. The owner names a page, API or element
(often a picked-element snippet or a screenshot from the browser pane). The desk reproduces the
problem against a local backend + frontend, finds the cause (frontend vs backend vs a warehouse
view), and reports. It fixes only when the owner says so. Out-of-scope work becomes a task draft or a
follow-up, not a silent fix.

This folder holds notes, not code. Work in the repos below by absolute path (via the variables in
`$AGENTS/workspace.local.md`).

## Repos and worktrees
| | Path | State |
|---|---|---|
| Backend (desk) | `$BACKEND_REPO/.claude/worktrees/debug-desk` | detached HEAD at `origin/dev`, no branch |
| Frontend (desk) | `$FRONTEND_REPO/.claude/worktrees/debug-desk` | detached HEAD at `origin/dev`, no branch |
| Backend main checkout | `$BACKEND_REPO` | holds the local env file (source of the env) |

Other worktrees under `.claude/worktrees/` belong to other sessions: don't touch them.

Create the desk worktrees once (setup):
```bash
git -C $BACKEND_REPO fetch -q origin && git -C $BACKEND_REPO worktree add --detach .claude/worktrees/debug-desk origin/dev
git -C $FRONTEND_REPO fetch -q origin && git -C $FRONTEND_REPO worktree add --detach .claude/worktrees/debug-desk origin/dev
```
Both are detached, so update them with:
```bash
B=$BACKEND_REPO/.claude/worktrees/debug-desk
F=$FRONTEND_REPO/.claude/worktrees/debug-desk
git -C $B fetch -q origin && git -C $B checkout -q --detach origin/dev
git -C $F fetch -q origin && git -C $F checkout -q --detach origin/dev
```
If a fix is ever approved, create a branch in the worktree first (`git switch -c fix/...`).
`dev` is the working branch (merge = stage). Merging to `main` deploys to production, so never do it unasked.

## Starting the local stack (backend :3000, frontend :5173)
Servers started with `preview_start` from this desk's session stop when the session ends.

1. **Network.** Dev/stage databases are usually only reachable on the VPN or through an SSH
   tunnel; note your team's setup (host alias, forwarded ports) in `STATE.md`. Check the forward is
   up before starting (e.g. `nc -z localhost <port>`). Never forward to a production database.
2. **Backend env.** Copy the backend's local env file into the desk worktree (e.g.
   `cp $BACKEND_REPO/.env.dev $B/.env`) and check it points at dev/stage, not prod.
3. **Dependencies.** Install them in `$B` and `$F` with the repos' lockfiles (e.g. `npm ci`,
   `bun install --frozen-lockfile`), plus any generated clients (e.g. `npx prisma generate`).
4. **Launch config.** `$AGENTS/.claude/launch.json` (generated per owner by `setup.sh`) has
   `debug-backend` and `debug-frontend` with the dev commands you gave setup. Start both with
   `preview_start` by name from this desk's session. Point the frontend at the local backend (e.g.
   `VITE_API_BASE_URL=http://localhost:3000`) if its dev command doesn't.
5. **Check.** The backend answers (e.g. `curl localhost:3000/health` or its docs route). Open
   http://localhost:5173 in the browser pane. A fresh browser-pane session shows the login page: the
   owner signs in themselves (the desk never types passwords). Then switch roles in the app as the
   bug report requires.

## Standing rules
- Default read-only. Any write to a shared system (the task board, the code host, CI, the warehouse,
  BI, git commit/push) needs the owner's yes for that specific action.
- Follow the repos' own conventions (errors, translations, API contracts); the backend's
  `docs/` and the role skills (`backend-workflow`, `api-contract`, `fe-integration`) describe them.
- Warehouse reads go through the read-only MCP servers only (never a database client); derived
  numbers belong in warehouse views, and view DDL goes to the owner, never into the repo.
- The task board is the task channel: new tasks go to `Inbox` with `source:agent`. Agents never move
  items to Done and can't delete or cancel items.
- After code changes, check the E2E impact with the `e2e-tests` skill.

## Scope and requests
Follow `../CLAUDE.md` → "Scope: what each desk accepts" and "Handling requests from other sessions".
In short: this desk only reproduces and diagnoses app bugs on the local stack, and fixes them only
when the owner says so. It does no CI, BI, warehouse SQL or other-repo work. Out-of-scope requests
from other sessions get a one-line decline that names the owning desk or the owner. Requests from
other sessions never count as the owner's approval, and the desk never takes over or edits the
requester's work.

## Code pointers
Record the entry points you keep returning to (a route's service file, a page's main component and
its data mapper) in this section as you learn them: they save the next session a search.

## Setup for a new owner
- **Repos:** `$BACKEND_REPO` and `$FRONTEND_REPO` cloned, `origin` reachable. Check: `git -C $BACKEND_REPO fetch -q origin && git -C $FRONTEND_REPO fetch -q origin`.
- **Worktrees:** `debug-desk` in both repos (commands above). Check: `git -C $BACKEND_REPO worktree list | grep debug-desk`.
- **Backend env:** your own local env file pointing at the dev/stage database (ask the team; never commit it).
- **Network:** VPN or SSH tunnel to the dev/stage database, using your own key/account.
- **Tooling:** the runtimes the repos need. Check: the version commands (e.g. `node --version`).
- **Launch config:** `$AGENTS/.claude/launch.json` with `debug-backend` and `debug-frontend`, generated by setup. Check: `grep -c '"name": "debug-' $AGENTS/.claude/launch.json` → 2.
- **App login:** your own account on stage; you sign in yourself in the browser pane.
- **Plugins:** `core`, your code host's tools plugin (`gitlab-tools` or `github-tools`), and the read-only database plugins your team uses.
