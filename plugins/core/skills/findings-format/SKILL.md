---
name: findings-format
description: The one format for reporting review findings — every finding labelled confirmed, needs checking or rejected, only confirmed ones get a severity, numbered under Must / Should / Nice to fix, and every report ends with what was not checked. Use whenever you report findings from a code review, an MR or release check, a nightly finding, a security pass or a data check, so people can say "fix 2 and 5" and nothing unchecked reads as clean.
---

# Findings format

Use this for every list of findings you post: a code review (the `reviewer` agent), the release
agent's MR comment, nightly findings, a security pass. One format, so people can act on it fast
and nothing unchecked reads as clean.

## Certainty first, then priority

Every finding gets exactly one certainty label:

- **confirmed**: you saw it fail, or the code or data proves it (a `file:line` and a concrete
  input, state or caller that breaks; a query result). Only confirmed findings get a severity.
- **needs checking**: it may be real but one fact is missing. Name that fact ("needs checking:
  whether `/invoices/export` is reachable without a token in stage"). No severity: it isn't proven.
- **rejected**: you looked and it is not a problem. Keep it in the report, with why, so the same
  claim isn't raised again without new evidence.

Severity (confirmed only): **high** (wrong numbers people decide on, data or access exposed, a
release that breaks a page), **medium** (wrong in an edge case, a contract break someone will hit),
**low** (works, but fragile or unclear).

A missing best practice or a defence-in-depth idea is not a finding. Mention it under Nice to fix
at most.

## Layout

```
Verdict: <one line: ACCEPT / CHANGES NEEDED, or GREEN / RED / PENDING with the reason>

Must fix
1. [confirmed, high] path/file.ts:123: <what fails, with the input or scenario> → <fix>. If skipped: <what happens>.
2. [confirmed, medium] …

Should fix
3. [needs checking: <the missing fact>] path/file.ts:40: <what may fail> → <how to settle it>.

Nice to fix
4. [confirmed, low] …

Rejected
- <the claim> — <why it's not a problem> (so it isn't raised again)

Not checked: <what you could not read or run, and why: "e2e report missing", "no stage data for
role X", "frontend has no tests">. Write "Not checked: nothing" only when that is true.
```

- **Numbering runs through all sections**, so "fix 2 and 5" is unambiguous. Rejected items aren't
  numbered.
- **Must fix** = confirmed high or medium findings that block (a release goes RED on them).
  **Should fix** = confirmed low with real impact, and every needs-checking item that could be high.
  **Nice to fix** = the rest.
- Each numbered finding has four parts: what it is, the problem, the fix, and what happens if it's
  skipped (for Must fix).
- **"Not checked" is never empty by omission.** A check that didn't run, couldn't be read or was
  skipped goes here; it is never reported as passed (a check that didn't run is never green).

## Waivers

A person can waive one failing check or finding on one MR by commenting
`waive <check-id or finding number>: <reason>`. A waiver only counts when `infra/bin/waivers add`
accepts it (a person, a specific reason of 20+ characters, at most 3 per MR). List accepted and
rejected waivers in the report, under the finding they apply to.
