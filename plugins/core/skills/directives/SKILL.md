---
name: directives
description: Capture a lasting rule from an approver as a task board directive so it becomes a real change (a skill, guard or plugin rule) instead of something an agent is expected to remember. Use when an approver (routing.yaml `approvers`) says "from now on…", "always…", "never…", "every time…", "the rule is…", or otherwise sets a standing rule for how agents or the team work.
---

# Directives: owner rules become mechanisms

Agents don't remember across sessions, so a rule said once in a chat is lost. A directive is recorded
on the task board and stays open until the rule is **built into agent-ops**, as a skill rule, a guard rule, a
triage/release rule or a plugin setting, or until an approver decides not to.

## Who and when
- Only **approvers** (routing.yaml `approvers`, e.g. Sam and Alex) set directives. If someone else
  proposes a rule, treat it as a normal suggestion: a task for the question owner via `handoff`, not
  a directive.
- A directive is a standing rule ("from now on every backend task needs a handoff doc"). A one-off
  request ("fix this now") is not.

## How
1. Search the task board for open items labelled `directive` in the project: if the same rule is already
   there, comment on it instead (add the new wording or example).
2. Create a work item in the project you're working in (e.g. BIL, PRC or ANL), state `Inbox`, labels `directive`
   and `source:agent`, no assignee:
   - Title: `Directive: <the rule in one line>`
   - Description:
     ```
     ## Rule (verbatim)
     > <the person's exact words, quoted, with name and date>
     ## Meaning
     <your one-paragraph restatement: what agents must do or never do, and when it applies>
     ## Where it probably belongs
     <which skill / guard / agent in agent-ops, if you can tell>
     ```
3. Follow the rule yourself for the rest of this session. Tell the user in one line that it's
   recorded and will be built into agent-ops.

Triage routes directives to the question owner. A directive is done only when a change that enforces it is merged in
agent-ops (with the MR link in the closing comment), or when an approver closes it with a reason.
