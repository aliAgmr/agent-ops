---
name: agents-structure-changes-to-repo
description: Every structure change in the agents workspace must be pushed to the team's agent-ops repo (desks/ folder) via branch + merge request
metadata:
  type: feedback
---

Every change to the agents workspace's structure (CLAUDE.md, SETUP.md, setup.sh, templates/, .gitignore, .githooks, claude-memory, adding or removing desks, any desk's DESK.md) must be shipped to the team's agent-ops repo (the desks live in its `desks/` folder) in the same turn. STATE.md, workspace.local.md and other ignored files are personal and never committed.

**Why:** agent-ops is the team's shared source for every agent, and `desks/` is the template for these desks, so every owner gets the same setup when they clone or pull it.

**How to apply:** use a `desks/<topic>` branch in a worktree (never switch the branch of the folder the desks run in), push it and open a merge/pull request (no need to ask: the owner agreed to every `desks/*` push and merge request, and still reviews and merges each one) to `main` with the code host's MCP. The guard blocks pushes to `main`, so a maintainer merges. Pull after the merge. The full procedure is in the desks CLAUDE.md, "Structure changes always go to the repo".
