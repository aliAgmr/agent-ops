#!/usr/bin/env python3
"""Writes a service agent's MCP config, service/<agent>/.mcp.json, from its template
(service/<agent>/mcp.template.json) and routing.yaml. run-agent.sh calls it before every tick;
SETUP.md and install scripts call it for every agent once (`render-mcp.py all`).

  render-mcp.py <triage|analyst|release|nightly|jenkins-watch|agent-mcp|all> [--check]

The template lists the servers every team has (the task board) and optional ones, each with
`"requires": [ENV, …]`: a server whose required variables are empty in the env file is left out, so a
team without, say, ClickHouse gets no failing ClickHouse server. `"code_hosts": "read" | "release"`
adds one MCP server per routing.yaml code host:
  - gitlab: @zereight/mcp-gitlab, GITLAB_READ_ONLY_MODE true (release: false), token CODEHOST_<KEY>_TOKEN
    (release: CODEHOST_<KEY>_RELEASE_TOKEN);
  - github: GitHub's MCP server (github.com: the hosted one over HTTP; Enterprise: the container, with
    GITHUB_HOST), read-only unless release.
The first host of each type is named `gitlab` / `github` (the names the agents' settings allow);
another host of the same type is `<type>-<key>` and needs its own allow rules.
The file holds `${VAR}` references only (Claude Code expands them from the environment), never values.
`--check` prints the result instead of writing it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import adapters as A  # noqa: E402

ROOT = HERE.parents[1]
AGENTS = ["triage", "analyst", "release", "nightly", "jenkins-watch", "agent-mcp"]


def code_host_servers(routing: dict, access: str) -> dict:
    out, used = {}, set()
    for key, spec in (routing.get("code_hosts") or {}).items():
        kind = (spec or {}).get("type") or "gitlab"
        url = ((spec or {}).get("url") or "").rstrip("/")
        name = kind if kind not in used else f"{kind}-{key}"
        used.add(kind)
        k = A.codehost_key(key)
        token = f"${{CODEHOST_{k}_RELEASE_TOKEN}}" if access == "release" else f"${{CODEHOST_{k}_TOKEN}}"
        if kind == "gitlab":
            out[name] = {"command": "npx", "args": ["-y", "@zereight/mcp-gitlab"],
                         "env": {"GITLAB_API_URL": f"{url}/api/v4", "GITLAB_PERSONAL_ACCESS_TOKEN": token,
                                 "GITLAB_READ_ONLY_MODE": "false" if access == "release" else "true",
                                 "USE_PIPELINE": "true"}}
        elif kind == "github":
            host = url.split("://", 1)[-1]
            if host in ("github.com", "www.github.com"):
                headers = {"Authorization": f"Bearer {token}"}
                if access != "release":
                    headers["X-MCP-Readonly"] = "true"
                out[name] = {"type": "http", "url": "https://api.githubcopilot.com/mcp/", "headers": headers}
            else:
                args = ["run", "-i", "--rm", "-e", "GITHUB_PERSONAL_ACCESS_TOKEN", "-e", "GITHUB_HOST",
                        "ghcr.io/github/github-mcp-server"]
                if access != "release":
                    args.append("--read-only")
                out[name] = {"command": "docker", "args": args,
                             "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token, "GITHUB_HOST": url}}
        else:
            raise ValueError(f"no MCP server known for code host type {kind!r} ({key})")
    return out


def render(agent: str, routing: dict, env: dict) -> dict:
    tpl = json.loads((ROOT / "service" / agent / "mcp.template.json").read_text())
    servers = {}
    for name, spec in (tpl.get("mcpServers") or {}).items():
        spec = dict(spec)
        req = spec.pop("requires", [])
        if all(env.get(v) for v in req):
            servers[name] = spec
    if tpl.get("code_hosts"):
        servers.update(code_host_servers(routing, tpl["code_hosts"]))
    return {"mcpServers": servers}


def main(argv) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    agents = AGENTS if argv[0] == "all" else [argv[0]]
    routing, env = A.load_routing(), A.env_values()
    for agent in agents:
        if agent not in AGENTS:
            print(f"unknown agent {agent}", file=sys.stderr)
            return 2
        cfg = json.dumps(render(agent, routing, env), indent=2) + "\n"
        if "--check" in argv:
            print(f"# service/{agent}/.mcp.json\n{cfg}")
        else:
            (ROOT / "service" / agent / ".mcp.json").write_text(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
