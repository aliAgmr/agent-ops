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

## When the host's network address changes

If the machine that runs Plane, n8n and the service agents gets a new address (a new DHCP lease,
Wi-Fi instead of a cable), everything that names that address breaks at once: the agents' tracker
URL, Plane → n8n webhooks and n8n's calls back to Plane. Avoid it from the start:

- `TRACKER_BASE_URL=http://127.0.0.1` in the host's env file when Plane runs on the same host;
- the Plane webhook at `http://host.docker.internal:5678/webhook/plane` and
  `TRACKER_FLOW_URL=http://host.docker.internal` for n8n (`infra/plane/install.sh` already allows
  that host for webhooks);
- people's clients use the public/reverse-proxy address, never the host's LAN address.

Related: an outage can leave `uvx` re-downloading the tracker MCP server's dependencies on a slow
link, so its first start times out. Warm it once by hand (`uvx --from <package>==<version> python -c 1`).
