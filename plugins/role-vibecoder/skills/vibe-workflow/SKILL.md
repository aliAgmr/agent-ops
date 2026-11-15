---
name: vibe-workflow
description: How a vibe coder takes one task on a project without teams from the board to a merge request with an AI agent — read the task and its plain-language brief, restate the plan in plain words and get the person's OK before touching code, work on a branch, make the smallest complete change, check it really works, open the MR with an explanation, and move the task to In Review. Use whenever the user starts, continues or finishes work on a task of a project without teams (routing.yaml `team_labels: false`), or asks "how do I do this task" in one of its repos.
---

# Vibe workflow

The person you work with directs you; they may not read the code. Your job is to do the work well
**and** make every step understandable, so they can decide with you. A vibe-coded project is only
as good as the care taken in each change.

## 1. Understand before you touch anything
1. Open the task on the board. Read the description, the `## Plain-language brief`, the
   `## Analysis` if there is one, the comments, the parent and linked items (`task-protocol`).
2. Read the repo's own rules first: its rules file (`CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`,
   whichever it has), the README, and any decisions log or spec the rules file points to. When the
   rules file names the part of the code that owns your change (a domain or module doc), read that
   too. The project's gates (the checks its CI runs) are the law: never bypass one, fix the cause.
   Reuse the components, helpers and formatting utilities the repo already has.
3. Open the files the brief names and check they say what the brief says. If the brief is wrong or
   stale, say so in a task comment (it helps the next person).

## 2. Say the plan back, get an OK
Before any edit, tell the person in plain words (see `explain-change`):
- what you understood the task to be, and what is **out of scope**;
- which files you will change and why, one line each;
- how you will check it works;
- what could break, and anything you are unsure about, as a question.

Wait for their OK. If the task is vague, ask up to three short questions (what should the user
see? which roles? what if there is no data?) rather than guessing.

## 3. Branch, then change
1. Assign the task to the person (they picked it) and move it to `In Progress` with a short comment.
2. Branch from an up-to-date `main`: `git switch main && git pull` then
   `git switch -c <key>-<number>-<short-slug>` (e.g. `lab-12-order-deadline`). Never commit on
   `main`, never push to it (the guard and the protected branch stop it anyway).
3. Make the change following `safe-change`: the smallest complete change, every line traceable to
   the task.
4. Commit in small steps with clear messages (`LAB-12: show the order's deadline in the list`).

## 4. Check it really works
Follow `check-before-done`: run the repo's checks, open it locally and look, test the edge cases,
run the `reviewer` agent. Nothing is done on "it should work".

## 5. Open the merge request
1. Push the branch and open a merge request (pull request on GitHub) into `main` with the code-host
   tools you installed (`gitlab-tools` or `github-tools`), or the link git prints. Title:
   `<KEY>-<n>: <what changes for the user>`.
2. The description follows `explain-change`: what and why, what changed per file, how it was
   checked (commands and results, screenshots for UI), what could break, what was not checked.
3. Never merge it yourself (the guard blocks it). One of the project's `merge_to_main` people
   (routing.yaml) reviews and merges it. When a merge to `main` deploys the app at once, say so in
   the MR.
4. On the task: post the done comment (`task-protocol`) with the MR link and the reviewer's verdict,
   and move it to `In Review`. It goes to `Done` when the MR is merged.

## When it gets hard
- A gate or test fails: read why, explain it to the person, fix the cause. Never loosen the check
  (`scope-guard` rule 10).
- The change needs the other repo too (an API and the page that uses it): `both-sides`.
- You are going in circles (the same error twice, the plan keeps growing): stop, summarise what you
  tried and what you saw, and ask the person how to proceed.
