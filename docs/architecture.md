# Architecture

agent-ops has two halves that share one repository: **people's agents** (Claude Code, Cursor, Codex
or Kimi on each person's machine) and **service agents** on an always-on **agent host**. They meet
on the **task board**; everything they do to shared systems goes through read-only credentials, the
**guard**, and the **action journal**.

```mermaid
flowchart TB
  subgraph Laptops["People's machines · macOS / Linux / Windows"]
    CC["Claude Code<br/>core + role plugins"]
    OT["Cursor · Codex · Kimi<br/>rendered by the agent-ops installer"]
    HK["hooks: guard · journal · session notes"]
    CC --> HK
    OT --> HK
  end

  subgraph Host["Agent host · macOS launchd or Linux systemd"]
    RA["run-agent.sh<br/>lock · precheck · budget · breaker"]
    SA["service agents<br/>triage · analyst · release · nightly"]
    AM["agent-mcp<br/>ask · messages · blame · suspects · lineage · revert_plan"]
    JR[("journal")]
    LN[("lineage")]
    QC["quality · trap checks"]
    AD["adapters<br/>tracker · code host"]
    RC["/srv/repos<br/>read-only clones"]
    WK["wake listener"]
    RA --> SA
    AM --> JR
    AM --> LN
    SA --> QC
  end

  subgraph Systems["Shared systems"]
    TB[("Task board<br/>Plane · your adapter")]
    CH["Code host<br/>GitLab · GitHub"]
    DS[("Data sources<br/>Postgres · ClickHouse · Metabase · Jenkins · Figma<br/>read-only users")]
    TG["Telegram"]
  end

  HK -->|"POST /journal"| AM
  CC -->|"MCP: board · code host · data"| TB
  CC --> CH
  CC --> DS
  OT --> TB
  CC -->|"ask · blame · lineage"| AM
  SA <-->|"MCP"| TB
  SA --> CH
  SA --> DS
  SA --> RC
  RC -->|"fetch, read-only token"| CH
  AD --- TB
  AD --- CH
  RA --> AD
  AM --> AD
  TB -->|"webhook"| WK --> RA
  AM --> TG
```

## Layers of safety

1. **Credentials** decide what is possible: read-only database users, a CI user without build
   rights, Developer-only code-host access with `main` protected, read-only tokens for the agents.
2. **The guard** (`plugins/core/scripts/guard.sh`) decides what an agent may try: it blocks
   database writes, CI runs, merges and deploys, pushes to `main`, deletions on the board, and more.
3. **Allow lists** decide what a service agent can call at all (`defaultMode: dontAsk`).
4. **The journal** records what happened, with the value before, so it can be blamed and undone.

## Where to go next

- `docs/reference/` — the full reference, built into one HTML page by `docs/reference/build.py`.
- `docs/security.md` — the security model.
- `docs/clients.md` — what each AI tool supports.
- `infra/adapters/README.md` — adding a task board or code host.
