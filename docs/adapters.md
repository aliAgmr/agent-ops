# Adapters

agent-ops talks to your **task board** and your **code host** through two small Python interfaces,
so you can use the tools your team already has.

| | Built in | Add your own |
|---|---|---|
| Task board | Plane (Cloud or self-hosted Community Edition) | Jira, Linear, GitHub Issues…: one `TrackerAdapter` class |
| Code host | GitLab (gitlab.com or self-managed), GitHub (github.com or Enterprise) | Bitbucket, Gitea…: one `CodeHostAdapter` class |

Pick them in `routing.yaml`:

```yaml
tracker: plane
code_hosts:
  main: { type: gitlab, url: https://gitlab.example.com }
  oss:  { type: github, url: https://github.com }
```

The interfaces, every method, the CLI and the steps to add an adapter are in
[`infra/adapters/README.md`](../infra/adapters/README.md). Remember that agents reach the same tools
through MCP servers: a new adapter also needs an MCP server entry for that tool in
`service/*/.mcp.json` and the core plugin.
