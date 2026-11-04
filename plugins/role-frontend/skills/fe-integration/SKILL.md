---
name: fe-integration
description: Build or change a page in the frontend from a design handoff and an API contract — page folder layout, API helpers, data fetching, components, dates, translations, URL filters — then ship it through a merge request to dev. Use for any frontend task that adds or changes a page, table, filter, chart or API call.
---

# Frontend integration

The examples assume a typical SPA (React + TypeScript + Vite, a component library, a data-fetching
library such as TanStack Query, a router). Put your repo's real stack and folder conventions in its
own docs (or a team skill) and follow those when they differ.

## Before coding

1. The task has a **design handoff** (Figma frames for every state) and, if it uses new data, an
   **API contract** from backend: the contract comment (`[agent:…-backend] API contract v<n>: …`)
   with a link to the handoff doc (`docs/frontend-handoff/<feature>.md` in the backend repo, read
   it there; it's the source of truth). If either is missing, comment on the task and use `handoff`
   to ask; don't guess field names.
2. Check that every element in the design maps to a field in the contract. List the gaps on the task.

## Where things go (follow the existing pages)

```
src/pages/<area>/<page>/
  index.tsx              # page component
  translations.ts        # page strings (per UI language)
  api/types.ts           # response/request types — field names exactly as in the API contract
  api/index.ts           # request helpers built on the shared API client
  columns/index.tsx      # table columns
```

- All HTTP goes through the shared API client (auth, language header, token refresh, error toasts).
  Don't call `fetch` directly.
- Data loading with the data-fetching library; pass the abort `signal` through.
- Filters/tabs that should survive reload live in the URL.
- Reuse the shared components before writing new ones.
- Dates: show them in the calendar and time zone the design says; send what the contract says.
- Text direction and language as the design says. Server values may come translated; don't
  translate identifiers (`*_code`, `*_id`).
- Every state from the design: loading, empty, error, no-permission.

## Ship

1. Branch from `dev`: `feature/<short-name>`; merge/pull request to `dev` (merge to `dev` = stage).
   Never `main`.
2. Lint and build must pass before the MR.
3. MR description: task link, screenshots of each state, which API contract version it uses.
4. Move the task to `In Review` with the done comment (`task-protocol`). An approver reviews it and
   moves it on (a maintainer merges); never move it past `In Review` yourself.
5. When frontend E2E tests exist, add/update them in the same MR (the release agent will run them).
