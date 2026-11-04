# Operations

Day-to-day running of the host: deploys, pausing agents, logs, alerts, budget, common failures,
blame and revert, lineage and trap checks, tests, adding people, projects and agents. It all lives
in the reference: [`docs/reference/sections/07-operations.md`](reference/sections/07-operations.md).

The short version:

```bash
# what did triage do last?
tail -1 ~/Library/Logs/agent-ops/triage/$(date +%F).jsonl | jq '{outcome, cost: .result.total_cost_usd}'
# who changed BIL-123?
infra/bin/journal blame BIL-123
# what is the dashboard built from?
infra/bin/lineage upstream /dashboard
# are the data traps healthy?
infra/bin/traps run
```

On Linux the logs are under `/var/log/agents/`.
