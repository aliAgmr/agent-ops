"""Configuration for the adapters and the host scripts: the repo's YAML files (routing.yaml,
projects.yaml, people.yaml) and the host's env file. Standard library only: uses PyYAML when it is
installed, otherwise a small reader for the YAML subset these files use (block maps and lists,
`- key: value` list items, inline `{a: b}` / `[a, b]`, quoted strings, comments)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]


def repo_root() -> Path:
    return Path(os.environ.get("AGENT_OPS_CONFIG_ROOT") or ROOT)


# ------------------------------------------------------------------------------------------- env

def env_values(path: Optional[str] = None, *, process: bool = True) -> Dict[str, str]:
    """KEY=value lines of the env file (AGENT_OPS_ENV or ~/.config/agent-ops/env), then the process
    environment on top (process=True), so a value exported by the caller wins."""
    p = Path(path or os.environ.get("AGENT_OPS_ENV") or "~/.config/agent-ops/env").expanduser()
    out: Dict[str, str] = {}
    if p.is_file():
        for line in p.read_text().splitlines():
            m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if m:
                v = m.group(2).strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                    v = v[1:-1]
                out[m.group(1)] = v
    if process:
        out.update({k: v for k, v in os.environ.items() if v != ""})
    return out


# ------------------------------------------------------------------------------------------ YAML

def load_yaml(path: Path) -> Any:
    text = Path(path).read_text()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return parse_yaml(text)


def _strip_comment(line: str) -> str:
    out, q = [], None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _scalar(s: str) -> Any:
    s = s.strip()
    if s == "" or s in ("~", "null", "Null", "NULL"):
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    if s.startswith("{") or s.startswith("["):
        v, _ = _flow(s, 0)
        return v
    if s in ("true", "True", "yes"):
        return True
    if s in ("false", "False", "no"):
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return s


def _flow(s: str, i: int) -> Tuple[Any, int]:
    """Parses an inline {…} or […] starting at s[i]."""
    def skip(j):
        while j < len(s) and s[j] in " \t":
            j += 1
        return j

    def token(j, stops):
        j = skip(j)
        if j < len(s) and s[j] in "{[":
            return _flow(s, j)
        if j < len(s) and s[j] in "'\"":
            q = s[j]
            k = s.index(q, j + 1)
            return s[j + 1:k], k + 1
        k = j
        while k < len(s) and s[k] not in stops:
            k += 1
        return _scalar(s[j:k]), k

    i = skip(i)
    if s[i] == "[":
        out, i = [], i + 1
        while True:
            i = skip(i)
            if s[i] == "]":
                return out, i + 1
            v, i = token(i, ",]")
            out.append(v)
            i = skip(i)
            if s[i] == ",":
                i += 1
    out_d: Dict[str, Any] = {}
    i += 1
    while True:
        i = skip(i)
        if s[i] == "}":
            return out_d, i + 1
        k = s.index(":", i)
        key = s[i:k].strip().strip("'\"")
        v, i = token(k + 1, ",}")
        out_d[key] = v
        i = skip(i)
        if s[i] == ",":
            i += 1


def parse_yaml(text: str) -> Any:
    lines: List[Tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = _strip_comment(raw)
        if line.strip():
            lines.append((len(line) - len(line.lstrip(" ")), line.strip()))

    def block(i: int, indent: int) -> Tuple[Any, int]:
        if i >= len(lines):
            return None, i
        if lines[i][1].startswith("- ") or lines[i][1] == "-":
            out: List[Any] = []
            while i < len(lines) and lines[i][0] == indent and (lines[i][1].startswith("- ") or lines[i][1] == "-"):
                rest = lines[i][1][1:].strip()
                if not rest:
                    v, i = block(i + 1, lines[i + 1][0]) if i + 1 < len(lines) and lines[i + 1][0] > indent else (None, i + 1)
                    out.append(v)
                    continue
                m = re.match(r"^([^:{\[\"']+?):(\s+(.*))?$", rest)
                if m and not rest.startswith(("{", "[")):
                    # a map that starts on the dash line: re-read it as a block at the inner indent
                    inner = indent + 2
                    lines[i] = (inner, rest)
                    v, i = block(i, inner)
                    out.append(v)
                else:
                    out.append(_scalar(rest))
                    i += 1
            return out, i
        d: Dict[str, Any] = {}
        while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
            m = re.match(r"^(\"[^\"]*\"|'[^']*'|[^:]+?):(?:\s+(.*)|$)", lines[i][1])
            if not m:
                raise ValueError(f"cannot read YAML line: {lines[i][1]!r}")
            key = m.group(1).strip("'\"")
            val = m.group(2)
            if val is not None and val.strip() not in ("", "|", ">"):
                d[key] = _scalar(val)
                i += 1
            elif val is not None and val.strip() in ("|", ">"):
                raise ValueError("block scalars are not supported without PyYAML")
            else:
                i += 1
                if i < len(lines) and lines[i][0] > indent:
                    d[key], i = block(i, lines[i][0])
                elif i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                    d[key], i = block(i, indent)
                else:
                    d[key] = None
        return d, i

    if not lines:
        return None
    v, _ = block(0, lines[0][0])
    return v


# ---------------------------------------------------------------------------- the repo's config

def load_routing(root: Optional[Path] = None) -> Dict[str, Any]:
    return load_yaml((root or repo_root()) / "routing.yaml") or {}


def load_projects(root: Optional[Path] = None) -> Dict[str, Any]:
    p = (root or repo_root()) / "projects.yaml"
    return ((load_yaml(p) or {}).get("projects") or {}) if p.is_file() else {}


def load_people(root: Optional[Path] = None) -> Dict[str, Any]:
    """people.yaml, falling back to people.template.yaml (the example team)."""
    root = root or repo_root()
    for name in ("people.yaml", "people.template.yaml"):
        p = root / name
        if p.is_file():
            return load_yaml(p) or {}
    return {}


def all_repos(routing: Dict[str, Any], *, host: Optional[str] = None, release_only: bool = False) -> List[Dict[str, Any]]:
    """Every repo in routing.yaml (default teams and every project's teams), de-duplicated by
    (host, path). `release_only` drops repos marked `release: false`."""
    teams = [routing.get("teams") or {}] + [(p or {}).get("teams") or {} for p in (routing.get("projects") or {}).values()]
    seen, out = set(), []
    for t in teams:
        for team in t.values():
            for r in ((team or {}).get("repos") or []):
                if not isinstance(r, dict) or not r.get("path"):
                    continue
                if host and r.get("host") != host:
                    continue
                if release_only and r.get("release") is False:
                    continue
                key = (r.get("host"), r["path"])
                if key not in seen:
                    seen.add(key)
                    out.append(r)
    return out
