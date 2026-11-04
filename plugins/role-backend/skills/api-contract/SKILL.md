---
name: api-contract
description: Required for every backend task that adds or changes a route, DTO, permission or response field. It writes the API handoff doc (docs/frontend-handoff/<feature>.md in the backend repo), updates the related frontend task with the contract and the doc link (or creates that task), and posts the contract on the backend task. Use before moving any backend task to In Review, when a contract changes, or when frontend asks "what does the API return".
---

# API handoff (backend → frontend)

**Rule:** a backend task that touches the API isn't finished until all three steps below are
done. Do them before moving the task to `In Review`. If the task has no API change, write
`API handoff: none (no API change)` in the done comment instead.

1. **Handoff doc** in the backend repo, in the same MR:
   `docs/frontend-handoff/<feature>.md` (kebab-case, one file per page or feature; e.g.
   `invoices-list.md`). Update the file if it exists; never
   start a second one for the same page.
2. **Frontend task**, from the backend task's relations (`relates to`/`blocking`, parent/children):
   - It exists: post the contract comment (template below) on it, add the doc link to its links,
     and if the change affects work already in progress, say what to change.
   - It doesn't exist: create it with the `handoff` skill (team frontend, `Inbox`), with the doc
     link and the contract comment, and link it to the backend task.
   When a project's frontend lives in another repo (e.g. PRC's `acme/pricing-admin`), the same rules apply.
3. **Backend task:** the same contract comment, plus `API handoff: <doc link> · frontend: <task id>`
   in the done comment (`task-protocol`).

## Handoff doc template (`docs/frontend-handoff/<feature>.md`)

````
# <Page or feature> (<page title as the design shows it>) — frontend connection guide

Backend: MR <url> · task <backend task id> · frontend <frontend task id> · on stage since <date | "not yet">
Status: DRAFT (may still change) | FROZEN (frontend builds on it; changes need a new version)

## Who sees it
Roles and the permission code (from `GET /authentication/me` → `permissions`). Everyone else → 403.

## Conventions (same as the rest of the API)
Envelope, auth, money format (e.g. decimal strings in minor units), date and time-zone format,
locale headers. Mention only what differs from the other docs.

## Endpoints
`METHOD /path`: one table per endpoint of query/body params (required, default, notes, the
design control it belongs to). Pagination and sorting.

## Response
A real example from stage or the E2E run, trimmed to 1–2 rows, then a table:
field | type | **required/optional** | nullable | meaning in business words | unit.
Every field is marked required (always present) or optional (may be missing). That's what lets
backend and frontend deploy separately: removing or renaming a required field is breaking, and
frontend must handle an optional field being absent.

## Enums and states
For every enum or status field: all values, what each means, and the allowed transitions
(`DRAFT → SUBMITTED → APPROVED | REJECTED`). Who can trigger each transition. A new value is a
contract change: frontend must show something sensible for a value it doesn't know.

## Where each part of the design comes from
Each element in the design (from the design handoff's "Data per element") → its response field.

## Errors
One row per error: HTTP status · `error` code · when it happens · what the UI should do (show the
message, retry, redirect, disable the action). State the language of `error_message` if it isn't English.

## Caching and refresh
Which other screens or queries go stale after a write here, and how frontend refreshes them
(e.g. "after POST /invoices: refetch the invoice list and the balance card"), plus any server cache
and its TTL. Write `none` if nothing is cached.

## Out of scope / not in this version
What the design shows that the API doesn't provide yet, and the follow-up task.

## Changes
<date> v<n>: what changed for frontend (newest first). Breaking changes in bold.
````

## Contract comment (frontend task and backend task)

````
[agent:<person>-backend] API contract v<n>: <short name>

**Doc:** <link to docs/frontend-handoff/<feature>.md on the MR branch, or on dev once merged>
**Routes:** `GET /...`, `POST /...`   **Permissions:** <codes>
**Status:** MR <url> → on stage <date | "after merge">
**Changed since v<n-1>:** <list, or "first version">
**Frontend needs to:** <what to build or change; "nothing" if it's additive and unused>
````

## Rules

- The example response comes from a real call on stage (or the E2E run), not hand-written.
- Field names match the code exactly. Types: `string` for money (state the unit), `YYYY-MM-DD` for
  dates, ISO 8601 strings with a time zone for timestamps.
- Every field the design shows maps to a response field, or is listed under "Out of scope".
- Every response field is marked required or optional; every enum lists its values (and allowed
  transitions for states); every error says what the UI should do. A contract missing any of these
  stays `DRAFT`.
- When the contract changes later, bump the version: update the doc (add to "Changes"), post a new
  contract comment on the frontend task with "Changed since…", and say whether it breaks what was
  built. Never change a contract silently.
- Write in English; quote page titles and UI labels from the design in the design's language.
