# agent-ops reference

One self-contained HTML page describes all of agent-ops: what each piece does, how to set it up and
run it, how to rebuild it, live reports from your agent host, and the full change timeline.

## What's in it and where it comes from

| Part | Source | Updated by |
|---|---|---|
| Reference sections (overview, people's agents, service agents, infrastructure, setup, rebuild, operations, desks) | `docs/reference/sections/*.md` | whoever changes agent-ops, in the same branch |
| Timeline | `git log --first-parent` of `origin/main` (or `HEAD`): every merge request / pull request with its commits and files | the build |
| Agent runs | `<log root>/<agent>/*.jsonl` and `skipped.log` (last 30 days) | the build |
| Quality | the analyst run logs' `quality` and agent-mcp's `answers-quality.jsonl` (counts only) | the build |
| Tools | `service/*/.claude/settings.json`, `.mcp.json`, `plugins/*`, `guard.sh`, and the agents' run transcripts | the build |
| Teammates | agent-mcp's SQLite (counts only, no text) and the task board through `infra/adapters` (items created / closed) | the build |

Every report is empty, not an error, when its source doesn't exist yet, so the page builds from a
fresh clone. People appear only if `people.yaml` exists, by first name.

`build.py` redacts emails (to first names), IP addresses, the hosts in your env file and
`routing.yaml`, every term in `tools/leak-terms.txt` and token-like strings, and refuses to write
output that still contains an email or IP address.

## Build

```bash
# on the agent host, with the env file loaded (live reports)
cd /srv/agent-ops && ( set -a; . ~/.config/agent-ops/env; set +a; uv run --with markdown --with pyyaml python docs/reference/build.py )

# anywhere, from the repo alone (no task-board calls)
uv run --with markdown --with pyyaml python docs/reference/build.py --offline
```

Output: `docs/reference/out/index.html` (git-ignored) plus `out/data/*.json` for scripts. `--out DIR`
writes elsewhere.

## Publish

The page is one file with no external dependencies besides fonts. Put it wherever your team reads
docs: a static site (GitHub Pages, an internal web server), a wiki that takes HTML, or a private
Claude artifact. Schedule the build and publish daily on the host so the reports and timeline never
lag. Service agents never publish (guard rule 8): a person's session or a scheduled job does.

## Editing the sections

- One file per section; the first `#` heading is its title; the file name's number sets the order.
- Mermaid diagrams: a ```` ```mermaid ```` fence (rendered by the page).
- No emails, IPs, internal hostnames or secrets; people by first name; example names from the
  Acme team in `routing.yaml`. CI's leak check enforces it.
