#!/usr/bin/env python3
"""Scans a merge request's diff for what it removes from the API contract and the access checks,
and for risky additions, so the release agent judges facts instead of eyeballing a large diff.

  mr-scan <repo-path> <iid>

Kinds and patterns: knowledge/contract-patterns.yaml (removed routes, DTO fields, permission and
role checks, auth plugins; added SQL built by interpolation). An item counts as removed when it is
on removed lines and no added line brings it back (in the same file, or in any file for kinds with
`across_files`). Route names get the router's prefix from the repo clone in /srv/repos
(AGENT_REPOS_DIR).

Prints JSON: {"mr", "files", "removed": {kind: [{"name", "file"}]}, "added": {kind: [...]},
"error"}. A diff that can't be read gives "error" (the caller reports it as not checked, never
as clean). Read-only: reads the diff through infra/adapters (GitLab or GitHub; the repo's host
from routing.yaml) with the release token. Exit 0 always.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra"))
PATTERNS = Path(os.environ.get("AGENT_CONTRACT_PATTERNS", str(ROOT / "knowledge" / "contract-patterns.yaml")))
REPOS = Path(os.environ.get("AGENT_REPOS_DIR", "/srv/repos"))


def load_patterns(path: Path = PATTERNS) -> Dict[str, Dict[str, Any]]:
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    out = {}
    for kind, spec in data.items():
        if not (kind.startswith("removed_") or kind.startswith("added_")) or not isinstance(spec, dict):
            continue
        out[kind] = {**spec, "rx": re.compile(spec["pattern"]),
                     "ignore_rx": re.compile(spec["ignore"]) if spec.get("ignore") else None,
                     "prefix_rx": re.compile(spec["prefix"]) if spec.get("prefix") else None}
    return out


def matches_glob(path: str, globs: List[str]) -> bool:
    for g in globs:
        if fnmatch.fnmatchcase(path, g) or ("**/" in g and fnmatch.fnmatchcase(path, g.replace("**/", ""))):
            return True
    return False


def split_diff(diff: str) -> tuple[str, str]:
    """(removed lines, added lines) of one file's unified diff, each joined with newlines."""
    rem, add = [], []
    for line in diff.splitlines():
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            rem.append(line[1:])
        elif line.startswith("+"):
            add.append(line[1:])
    return "\n".join(rem), "\n".join(add)


def names(spec: Dict[str, Any], text: str, prefix: str = "") -> List[str]:
    out = []
    for m in spec["rx"].finditer(text):
        groups = [g or "" for g in m.groups()] or [m.group(0)]
        fmt = spec.get("name")
        if fmt:   # {1}, {2:upper}, {prefix}
            def one(x, groups=groups):
                g = groups[int(x.group(1)) - 1] if int(x.group(1)) <= len(groups) else ""
                return g.upper() if x.group(2) == "upper" else g
            name = re.sub(r"\{(\d+)(?::(upper))?\}", one, fmt.replace("{prefix}", prefix.rstrip("/")))
        else:
            name = groups[0]
        name = re.sub(r"\s+", " ", name).strip()
        if not (spec.get("ignore_rx") and spec["ignore_rx"].search(name)):
            out.append(name)
    return out


def file_prefix(spec: Dict[str, Any], repo: str, path: str) -> str:
    if not spec.get("prefix_rx"):
        return ""
    p = REPOS / repo / path
    try:
        m = spec["prefix_rx"].search(p.read_text(errors="replace"))
    except OSError:
        return ""
    return m.group(1) if m else ""


def scan(changes: List[Dict[str, Any]], patterns: Dict[str, Dict[str, Any]], repo: str = "") -> Dict[str, Any]:
    """changes: [{"new_path", "old_path", "diff"}] (CodeHostAdapter.merge_request_diffs)."""
    removed: Dict[str, List[Dict[str, str]]] = {}
    added: Dict[str, List[Dict[str, str]]] = {}
    for kind, spec in patterns.items():
        rem_items, add_names_all = [], set()
        for ch in changes:
            path = ch.get("new_path") or ch.get("old_path") or ""
            if not matches_glob(path, spec.get("files", ["*"])):
                continue
            rem, add = split_diff(ch.get("diff") or "")
            prefix = file_prefix(spec, repo, path)
            if kind.startswith("added_"):
                for n in names(spec, add, prefix):
                    added.setdefault(kind, []).append({"name": n, "file": path})
                continue
            add_names = set(names(spec, add, prefix))
            add_names_all |= add_names
            for n in names(spec, rem, prefix):
                rem_items.append({"name": n, "file": path, "_back": n in add_names})
        if kind.startswith("removed_"):
            across = spec.get("across_files")
            gone = [{"name": r["name"], "file": r["file"]} for r in rem_items
                    if not r["_back"] and not (across and r["name"] in add_names_all)]
            uniq = {(r["name"], r["file"]): r for r in gone}
            if uniq:
                removed[kind] = list(uniq.values())
    return {"removed": removed, "added": added}


def fetch_changes(project: str, iid: str, routing: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """The MR's diff per file through the code-host adapter (GitLab MR or GitHub pull request); the
    repo must be in routing.yaml, which says its host. Token: CODEHOST_<KEY>_RELEASE_TOKEN, falling
    back to CODEHOST_<KEY>_TOKEN."""
    import adapters as A
    routing = routing if routing is not None else A.load_routing()
    entry = next((r for r in A.all_repos(routing) if r["path"] == project), None)
    if not entry:
        raise RuntimeError(f"repo not in routing.yaml: {project}")
    key = entry.get("host") or "main"
    g = A.codehost(routing, key, kind="release")
    if not g.token:
        raise RuntimeError(f"CODEHOST_{A.codehost_key(key)}_RELEASE_TOKEN is not set")
    return g.merge_request_diffs(project, int(iid))


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or not re.match(r"^[A-Za-z0-9._/-]+$", argv[0]) or not argv[1].isdigit():
        print("usage: mr-scan <repo-path> <iid>", file=sys.stderr)
        return 2
    project, iid = argv
    out: Dict[str, Any] = {"mr": f"{project}!{iid}", "files": 0, "removed": {}, "added": {}, "error": None}
    try:
        changes = fetch_changes(project, iid)
        out["files"] = len(changes)
        out.update(scan(changes, load_patterns(), project.rsplit("/", 1)[-1]))
    except Exception as e:  # noqa: BLE001 - unreadable is reported, never treated as clean
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
