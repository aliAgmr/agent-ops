---
name: both-sides
description: How to make a change that needs both repos of a project without teams — an API change in the backend repo and the page that uses it in the frontend repo — in the right order, with the API contract agreed first and two linked merge requests. Use when a frontend task needs data or an action the API doesn't offer yet, when a backend change alters a response a page reads, or when the frontend still uses sample data (e.g. a `TODO(backend)` marker) for something the task needs.
---

# Both sides (backend + frontend)

A vibe-coded frontend often still shows sample data in places (search for the marker the repo uses,
e.g. `TODO(backend)`, or a sample-data folder). A change that spans the API and the page goes in
this order.

1. **Agree the contract first.** Write down, in plain words and as JSON, what the endpoint takes and
   returns: path, method, who may call it (roles), request fields, response fields with types and an
   example, errors. When the backend repo keeps versioned API contracts (a contracts folder, an
   OpenAPI file, a contract check in its gates), add or update it there. Show the contract to the
   person before writing code on either side.
2. **Backend MR first.** Implement the endpoint with its permission check and tests (`safe-change`,
   `check-before-done`). Additive only: a new field or endpoint, never a removed or renamed one that
   any app still reads; search both repos for the field first.
3. **Frontend MR second,** on its own branch, replacing the sample data for that part with the real
   call. Keep the sample-data path working only if the README says the app must still run without
   the API.
4. **Link them:** each MR names the other, and the task (or its two children, one per repo, with
   `blocked by` in order) links both.
5. **Merge order:** backend first, then frontend. Tell the reviewer, because merging the page first
   would deploy a page calling an endpoint that doesn't exist yet.

When the person who owns the backend repo hasn't seen the API change, ask your person to involve
them before the backend MR is merged.
