"""agent-ops for Cursor and Codex: renders the team's Claude Code plugins (plugins/) for other clients.

The plugins stay the single source of truth. This package reads them and writes each client's own
files (skills, MCP servers, hooks, always-on rules); see clients/README.md.
"""
