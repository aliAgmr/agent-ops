---
name: e2e-tests
description: The mandatory workflow for keeping a backend's end-to-end HTTP test suite current — which tests to write, who writes them, how to read a red run. Use for ANY change that touches an API — a new or changed route, DTO, response field, permission, role, error, business rule, migration a route depends on, or a bug fix — and whenever the user asks to run, fix, extend or explain the E2E tests, a red e2e CI job, BUGS.md, or the nightly report.
---

# E2E tests — the workflow every API change follows

This skill assumes the backend repo has an end-to-end suite that drives the real app over HTTP
against a **fresh, isolated test database** built every run (and, if the app reads one, a
**read-only** warehouse). It covers every route: access per role, valid and invalid inputs,
response contracts, timing and business rules. The repo's own `docs/E2E.md` (or equivalent)
describes its commands and layout; read it before changing the harness, the seed or CI. Put your
repo's specifics (commands, suite names, personas) in that doc, not here.

## The rules (non-negotiable)

1. **Every task that changes an API also updates the E2E tests, in the same branch / MR.** New
   route → new spec entry + schema + behaviour tests. Changed behaviour → changed expectations.
   Removed route → removed spec entry. No exceptions for "small" changes.
2. **A separate agent writes the tests.** When implementing a feature or fix, spawn one agent whose
   only job is writing/updating the E2E tests (it does not touch the application code), alongside
   the agent(s) changing the code. Give it the intended behaviour (the route description, docs, the
   user's spec) — never "make the tests match the code".
3. **Tests encode the INTENDED behaviour, not what the code returns.** Where code and intent
   disagree, the test keeps the intent and fails.
4. **A red test means the code is wrong until proven otherwise.** Investigate the code first. Only
   change a test when it is provably wrong (contradicts the spec/route description, or its own
   assumption is false) — and say which test, why, and what changed. Never loosen an assertion to
   get green.
5. **Run the related E2E tests before calling a change done** — not the full suite. Report the
   result: counts, any failure and its cause. The full suite runs in CI (nightly and on merge
   requests into `main`); run it locally only when asked or when the change is cross-cutting.

**Never** point a run at a shared stage or production database. The runner should refuse to; don't
work around that guard.

## Which tests to run

Run only what the change can affect:

| Change | Run |
|---|---|
| Route(s) in one domain — behaviour, DTO, schema, error | that domain's suites + the generic access / input / inventory suites |
| Permission / role / scope | the affected domains + the access and role-catalogue suites |
| Response envelope, error handling, translations | the envelope and input suites + one domain suite |
| Auth, tokens, SSO, rate limits | the auth, rate-limit and access suites |
| Seed / fixtures / harness | the suites that use what changed; full run if the fixtures contract changed |
| Shared middleware, the app's lifecycle, a migration many routes read | **full run** |
| Query or performance change on a warehouse read | that domain + the timing suite (compare with the last timing report) |

## What a good suite has (if you are building one)

- **A spec per route:** method + path as registered, auth, required permission, whether it mutates,
  a valid sample request, the expected outcome for **every** persona (one test user per role, plus
  "no scope", "inactive" and "anonymous"), the request DTOs and the 200 response schema.
- **Generic suites over every spec:** the access matrix (incl. forged/expired tokens), invalid
  inputs generated from the DTOs, the response envelope, an inventory guard that fails while a
  served route has no spec, timing.
- **Behaviour suites per domain:** every documented rule, filter, error key, state change and scope
  narrowing.
- **A seed through the app's own services** producing a typed fixtures file; suites that need more
  state create it **through the API**, never by writing the database.
- **`BUGS.md`:** known bugs the suite encodes (`BUG-0xx`: route, intended, today, where); the report
  groups their failures separately from unattributed ones.

## What to do, by kind of change

- **New route:** spec entry (with access for every persona), response schema (strict where the key
  set is fixed), behaviour tests. The generic suites pick it up; the inventory guard fails until
  the spec exists.
- **Changed route** (new field, filter, rule, status or error): update the spec, the schema and the
  behaviour tests that pin the old behaviour.
- **New permission / role / scope change:** update the intended role → permission catalogue (and a
  persona for every new role) and every affected access map.
- **Migration / new table the seed or a route needs:** extend the seed and, if suites need the new
  state, the fixtures contract. Prove a fresh database still builds.
- **Bug fix:** if the bug is in `BUGS.md`, its tests already fail; the fix makes them pass — then
  **remove the row** in the same MR. If it is new, the test agent first writes a failing test for the
  intended behaviour (new `BUG-0xx`), then the fix turns it green.
- **Found a bug you are not fixing now:** write the intended-behaviour test anyway, title it
  `[BUG-0xx]`, add the row to `BUGS.md`.

## Writing tests that hold up

- Talk to the app only through the harness's client (records timing, handles tokens).
- Warehouse or live data changes daily: assert shapes, invariants and consistency between endpoints
  (list row = detail row, totals = Σ rows, scoped ⊆ admin), never exact business numbers.
- Tests share one database: give created entities unique titles, assert "contains"/deltas, never
  assume global counts, never change seeded rows other suites rely on.
- Dates and time zones: compute them with a helper that is independent of the app's own code;
  never hard-code months.

## Triage a red run

1. Read the summary's **unattributed failures** first; failures under a `BUG-0xx` heading are known.
   Check the environment banner (warehouse or network errors) before blaming code.
2. For each failure: read the assertion message (method, url, status, body) and the route's intended
   behaviour. Decide **code bug** (default) vs **test provably wrong**. Report which and why.
3. Code bug in scope of the task → fix the code. Out of scope → record it as a `BUG-0xx`.
4. Rerun the related suites until green. A full run only for cross-cutting changes or when asked.

## Checklist before finishing an API task

- [ ] Spec entry / schema / behaviour tests added or updated for every touched route
- [ ] Role catalogue, fixtures/seed updated if roles, fields or state changed
- [ ] `BUGS.md` updated (fixed rows removed, new known bugs added)
- [ ] Unit tests, lint and type checks green
- [ ] Related E2E suites run; result reported (counts, failures + cause). Full run only if the
      change is cross-cutting.
- [ ] Tests were written by a separate test agent, from the intended behaviour
