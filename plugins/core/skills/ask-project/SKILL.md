---
name: ask-project
description: Ask the team's project agent (agent-mcp MCP server) about parts of a project you can't see yourself — other repos (backend/frontend/data), application or warehouse data, CI builds and logs, merge requests and pipelines, task board history, why something was built a certain way. Use when the user's question or task depends on code, data or history outside the repos and tools you have, before guessing; e.g. "what does the backend return for X", "which table holds Y", "why did the last deploy fail", "who calls this API".
---

# Ask the project agent

The `agent-mcp` MCP server (from this plugin, authenticated with the user's task board token) gives
you a read-only expert on a whole project: every repo, the read-only databases and warehouse,
CI, the code host and the task board. You can only ask about projects the user is a member of on
the task board.

## When

- The answer is in a repo, database, warehouse table, CI job, MR/PR or task you can't
  open from here (e.g. you are in the frontend and need the backend's behaviour).
- Not for things you can check yourself in the current repo: do that directly.
- It **explains and guides; it never builds, edits or changes anything** for you. You do the work
  in your own repo/tools with what it tells you. Don't ask it to write files, open MRs, run jobs
  or change the task board.

## How

1. `list_projects()` → pick the project key (e.g. `BIL`).
2. One conversation per topic: `list_conversations(project)` and reuse a matching one, or
   `create_conversation(project, title)` with a short topic title. Note the `conversation_id`
   (e.g. in your task notes) so later questions on the topic go to the same conversation.
3. `ask(conversation_id, question, depth)` → returns a `ticket` immediately. Write the question
   as you would to a senior teammate: what you need, why, and what you already know (file names,
   error text, task id). `depth`: `quick` for a small lookup, `default` normally, `deep` for broad
   investigations (slower).
4. Poll `get_answer(ticket, wait_seconds=50)` until `status` is `done` (read `answer` and
   `sources`) or `error`. `queued`/`running` are normal: quick answers take about a minute, deep
   ones much longer. Keep polling; don't re-ask.
5. Follow-ups ("and who calls it?") go to the **same** `conversation_id`: it remembers the
   earlier questions and answers. Questions in one conversation run one after another.
6. `rename_conversation` / `close_conversation` when a topic is done.

## Using the answer

- Answers are written for you (an agent), in English, with sources (`repo/path:line`, SQL used,
  MR/PR, task). Verify anything you will build on, cite the sources to the user, and relay
  the gist in the user's language.
- Conversations are private to the user whose token created them.
- It never reveals secrets or credentials; don't ask for them.
