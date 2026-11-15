---
name: explain-change
description: How to explain a planned or finished change to a vibe coder and to whoever reviews the merge request — plain words, before → after for the user, what changed in each file and why, how it was checked, what could break, and what was not checked. Use for the plan you show before coding, every MR description and every done comment on a task of a project without teams, and whenever the person asks "what did you change" or "what does this do".
---

# Explain the change

Write for a smart person who doesn't read code all day. Short sentences, no jargon (or explain the
word right there), concrete examples. Never claim more than you checked.

## The shape (plan, MR description, done comment)

```
**What changes for users:** <before → after, in product words: who sees what, where>
**Why:** <the task's goal in one or two lines; link <KEY>-<n>>

**What I changed:**
- `<repo>: <path>` — <what this file does> → <what changed in it, and why>
- …

**How I checked it:**
- <command> → <result: e.g. "312 tests passed", "build OK">
- <page or endpoint> → <what you did and what you saw; screenshots before/after for UI>
- Edge cases: <empty list, no permission, very long text, phone width> → <result>

**What could break:** <other pages/endpoints using the same code; money, permissions or data paths;
how you made sure they still work>
**Not checked:** <what you couldn't run or see, and why; "nothing" only when true>
**Words used:** <only if needed: a term and its meaning in one line>
```

## Rules
- **Product words first, code second.** "Customers now see the deadline next to each order" before
  "added `deadline` to `OrderRow`".
- **One line per file**, saying what the file is for, not only what changed.
- **Numbers and outputs, not adjectives:** "312 passed, 0 failed", not "tests look good".
- **Honest gaps:** anything you didn't run goes under "Not checked", never into "How I checked it".
- **For a plan** (before coding), use the same shape in future tense, and end with the questions you
  need answered.
- Keep it short: a reviewer should understand the MR in a minute. Link to details instead of pasting
  logs.
