# Contributing to agent-ops

Thanks for helping. agent-ops is small on purpose: plain files, a few scripts, and standard-library
Python wherever possible, so a team can read all of it before trusting it with their tools.

## Ground rules

- **Safety first.** The hard rules (no database writes, no deploys or merges, no CI runs, no
  deleting task-board items, no editing the action journal) are enforced by credentials first and
  by `plugins/core/scripts/guard.sh` second. Any change that loosens one needs:
  - an issue that explains why;
  - tests in `tests/guard.test.sh`;
  - a line in `docs/security.md`.
- **Generic only.** Examples use the example team, "Acme" and `*.example.com`. Real names,
  emails, hosts, ids and secrets never go in. `tools/leak-check.py` fails CI on them.
- **Offline tests.** Every test fakes the external API it needs (a tiny local HTTP server, or a fake
  `curl`/`claude` first on `PATH`). No test may need a network, a token or a real account.
- **Small dependencies.** Host scripts run on the stock `python3` (3.9+) of macOS and Linux. Use
  `uv run --with …` for anything that needs a package, and pin the version.

## Development

```bash
git clone https://github.com/<you>/agent-ops.git && cd agent-ops
bash tools/lint.sh          # JSON, shell, skill front matter, leak check
bash tools/run-tests.sh     # every offline suite (or: bash tools/run-tests.sh guard journal)
```

Requirements: bash, git, jq, curl, python3 ≥ 3.9, [uv](https://docs.astral.sh/uv/), and Node ≥ 18
for the n8n flow tests. To validate the plugins, install Claude Code and run `claude plugin
validate .`.

## Pull requests

- **One topic per PR.** Explain what changes for a team that runs agent-ops, and how you tested it.
- **Version bumps:** raise the `version` of each plugin you changed in its `plugin.json`.
- **Docs:** update the matching `docs/reference/sections/*.md` in the same PR.
- **CI:** it runs lint and the leak check, plugin validation, the tests on Linux and macOS, and
  the client installer tests on Windows. All must pass.

## Adding support for another tool

- **Task board** (Jira, Linear, GitHub Issues…): implement `TrackerAdapter`
  (`infra/adapters/README.md`), then add an MCP server for it to the core plugin and the service
  agents.
- **Code host:** implement `CodeHostAdapter` the same way.
- **AI client:** add a renderer in `clients/agent_ops/render.py` and a column in
  `docs/clients.md`. State honestly what the client can't do: guard hooks, skills, rules.

## Reporting a security issue

Please don't open a public issue for a way around the guard or a token leak. Use GitHub's private
vulnerability reporting on this repository instead.
