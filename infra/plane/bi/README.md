# Plane → BI (read-only)

Your BI tool (Metabase, Superset, …) reads Plane's own Postgres **live**, through a read-only user
that sees only a `bi` schema of clean views. Nothing is copied, and nothing can write.

```
BI tool → (optional gateway :15435 ─ssh -R─→) agent host 127.0.0.1:15432 → plane-db:5432
          (bi-tunnel)                        (docker-compose.bi.yaml)
```

## Views (schema `bi`)

| View | One row per | Useful for |
|---|---|---|
| `tasks` | task (not deleted, not draft) | key, project, title, state + group, priority, team, source, labels, assignees, parent, created/started/completed, lead/cycle/age days, overdue, URL |
| `task_labels` / `task_assignees` | task × label / task × person | filters and breakdowns |
| `state_changes` | state move | flow, throughput, time in state, who moves what (`by_bot`) |
| `task_activity` | any field change | everything else |
| `comments` | comment | `by_agent` / `agent_tag` ([agent:…]), activity per person |
| `projects`, `people`, `cycles`, `cycle_tasks` | — | joins and labels (`people` has no secrets) |

Started = the first move into a state of group `started` (In Progress, In Review, …). Cycle time =
completed − started. Lead time = completed − created. Times are UTC.

## Setup (once)

1. **Views and user:** run `TRACKER_WORKSPACE=<slug> bash infra/plane/bi/apply.sh` yourself (the
   agents' guard doesn't let them open database clients). It creates `metabase_ro`, which can only
   read `bi` and is read-only with a 60 s statement timeout. The password goes to
   `~/.config/agent-ops/plane-bi.password` and is never printed. Task URLs use plane.env's `WEB_URL`.
2. **Port on the host:** `bash infra/plane/bi/expose.sh`. Publishes Postgres on `127.0.0.1:15432`
   only, and recreates just the database container (Plane is down for a few seconds).
3. **If the BI tool is elsewhere:** a tunnel to a gateway: `BI_TUNNEL=true TUNNEL_TARGET=… bash
   infra/macos/install.sh` (Linux: the `agent-bi-tunnel` service). It binds `0.0.0.0:15435` on the
   gateway by default (`BI_BIND`), so a BI tool in Docker there can reach it. What protects it: the
   password, read-only access to `bi` only, 10 connections, and a 60 s statement timeout.
4. **BI tool:** add a PostgreSQL database: host (gateway or this host), port, database `plane`, user
   `metabase_ro`, the password from the file above, schema `bi` only.

After a Plane upgrade, re-run `apply.sh`. If a view fails because Plane renamed a column, fix
`setup.sql`; the dashboards keep working once the view does.
