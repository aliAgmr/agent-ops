"""Adapters for the task board (tracker) and the code host. See README.md here.

    from adapters import tracker, codehost, codehost_for_url, all_repos
    t = tracker(role="triage")            # TRACKER_TRIAGE_TOKEN, routing.yaml `tracker:`
    g = codehost(key="main", kind="read") # CODEHOST_MAIN_TOKEN, routing.yaml `code_hosts.main`

Token per role (from the env file, or revert.env for kind/role "revert"):
  tracker:  triage → TRACKER_TRIAGE_TOKEN, release → TRACKER_RELEASE_TOKEN,
            nightly → TRACKER_NIGHTLY_TOKEN (or the file in TRACKER_NIGHTLY_TOKEN_FILE),
            admin → TRACKER_ADMIN_TOKEN, revert → TRACKER_REVERT_TOKEN (revert.env only)
  codehost: read → CODEHOST_<KEY>_TOKEN, release → CODEHOST_<KEY>_RELEASE_TOKEN (falls back to
            the read token), revert → CODEHOST_<KEY>_REVERT_TOKEN (revert.env only)
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .codehost.base import CodeHostAdapter
from .config import all_repos, env_values, load_people, load_projects, load_routing, load_yaml, repo_root
from .http import AdapterError
from .tracker.base import TrackerAdapter

TRACKERS = {"plane": ("tracker.plane", "PlaneTracker")}
CODEHOSTS = {"gitlab": ("codehost.gitlab", "GitLabHost"), "github": ("codehost.github", "GitHubHost")}
TRACKER_TOKENS = {"triage": "TRACKER_TRIAGE_TOKEN", "analyst": "TRACKER_TRIAGE_TOKEN",
                  "agent-mcp": "TRACKER_TRIAGE_TOKEN", "release": "TRACKER_RELEASE_TOKEN",
                  "nightly": "TRACKER_NIGHTLY_TOKEN", "admin": "TRACKER_ADMIN_TOKEN", "revert": "TRACKER_REVERT_TOKEN"}

__all__ = ["tracker", "codehost", "codehost_for_url", "codehost_key", "revert_env", "all_repos", "env_values",
           "load_routing", "load_projects", "load_people", "load_yaml", "repo_root", "AdapterError",
           "TrackerAdapter", "CodeHostAdapter", "TRACKERS", "CODEHOSTS"]


def _cls(table: Dict[str, Any], name: str):
    if name not in table:
        raise ValueError(f"unknown adapter {name!r}; built in: {', '.join(sorted(table))}")
    mod, cls = table[name]
    return getattr(importlib.import_module(f"{__name__}.{mod}"), cls)


def revert_env() -> Dict[str, str]:
    """~/.config/agent-ops/revert.env (AGENT_REVERT_ENV): the people's revert tokens, read only by
    `journal revert-apply`; run-agent.sh never loads it."""
    p = os.environ.get("AGENT_REVERT_ENV", "~/.config/agent-ops/revert.env")
    return env_values(p, process=False)


def codehost_key(key: str) -> str:
    return key.upper().replace("-", "_")


def tracker(config: Optional[Dict[str, Any]] = None, token: Optional[str] = None, *, role: str = "triage",
            env: Optional[Dict[str, str]] = None) -> TrackerAdapter:
    config = config if config is not None else load_routing()
    env = env if env is not None else env_values()
    if token is None:
        if role == "revert":
            token = revert_env().get("TRACKER_REVERT_TOKEN", "")
        else:
            var = TRACKER_TOKENS.get(role, role)
            token = env.get(var, "")
            if not token and env.get(f"{var}_FILE"):
                f = Path(env[f"{var}_FILE"]).expanduser()
                token = f.read_text().strip() if f.is_file() else ""
    return _cls(TRACKERS, config.get("tracker") or "plane")(env.get("TRACKER_BASE_URL", ""),
                                                           env.get("TRACKER_WORKSPACE", ""), token or "")


def codehost(config: Optional[Dict[str, Any]] = None, key: str = "main", token: Optional[str] = None, *,
             kind: str = "read", env: Optional[Dict[str, str]] = None) -> CodeHostAdapter:
    config = config if config is not None else load_routing()
    hosts = config.get("code_hosts") or {}
    if key not in hosts:
        raise ValueError(f"no code host {key!r} in routing.yaml code_hosts ({', '.join(hosts) or 'none'})")
    spec = hosts[key] or {}
    env = env if env is not None else env_values()
    if token is None:
        k = codehost_key(key)
        if kind == "revert":
            token = revert_env().get(f"CODEHOST_{k}_REVERT_TOKEN", "")
        elif kind == "release":
            token = env.get(f"CODEHOST_{k}_RELEASE_TOKEN") or env.get(f"CODEHOST_{k}_TOKEN", "")
        else:
            token = env.get(f"CODEHOST_{k}_TOKEN", "")
    return _cls(CODEHOSTS, spec.get("type") or "gitlab")(spec.get("url") or "", token or "")


def codehost_for_url(url: str, config: Optional[Dict[str, Any]] = None, *, kind: str = "read",
                     env: Optional[Dict[str, str]] = None) -> Optional[CodeHostAdapter]:
    """The configured code host whose hostname matches a git URL, or None."""
    config = config if config is not None else load_routing()
    def netloc(u: str) -> str:   # host[:port], without credentials
        return u.split("://", 1)[-1].split("/", 1)[0].split("@")[-1].lower()

    host = netloc(url)
    for key, spec in (config.get("code_hosts") or {}).items():
        h = netloc((spec or {}).get("url", ""))
        if h and h == host:
            return codehost(config, key, kind=kind, env=env)
    return None
