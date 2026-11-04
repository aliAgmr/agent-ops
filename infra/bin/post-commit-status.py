#!/usr/bin/env python3
"""Posts the `release-agent` commit status on a commit: the only write the release agent makes to
the code host. Works for GitLab and GitHub through infra/adapters; the repo must be in routing.yaml,
which also says its host. Token: CODEHOST_<KEY>_RELEASE_TOKEN (GitLab: api scope, Developer or
more; GitHub: "Commit statuses: write"), falling back to CODEHOST_<KEY>_TOKEN.

  post-commit-status.sh <repo-path> <sha> <success|failed|pending> "<description>" [target-url]

Idempotent: when the newest release-agent status on the commit already has this state and
description (a retry after a timeout), nothing is posted. A failed lookup just posts. Each post is
recorded in the action journal with the state before, so `journal revert-plan` can set it back.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import adapters as A  # noqa: E402

NAME = "release-agent"


def die(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


def main(argv) -> int:
    if len(argv) < 4:
        return die("usage: post-commit-status.sh <repo-path> <sha> <success|failed|pending> <description> [target-url]")
    repo, sha, state, desc = argv[:4]
    target = argv[4] if len(argv) > 4 else None
    if state not in ("success", "failed", "pending"):
        return die("state must be success|failed|pending")
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return die("bad sha")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", repo):
        return die("bad repo path")
    routing = A.load_routing()
    entry = next((r for r in A.all_repos(routing) if r["path"] == repo), None)
    if not entry:
        return die(f"repo not in routing.yaml: {repo}")
    key = entry.get("host") or "main"
    g = A.codehost(routing, key, kind="release")
    if not g.token:
        return die(f"CODEHOST_{A.codehost_key(key)}_RELEASE_TOKEN is not set")
    desc = desc[:250]
    prev = ""
    try:
        sts = g.statuses(repo, sha, name=NAME)
        if sts:
            last = sts[-1]
            prev = last.get("status") or ""
            stored = desc[:140] if g.name == "github" else desc     # GitHub keeps 140 characters
            if prev == state and (last.get("description") or "") == stored:
                print(f"already posted {state} on {repo}@{sha[:8]}, not posting again")
                return 0
    except A.AdapterError:
        pass                                  # a failed lookup just posts
    try:
        g.post_status(repo, sha, state, NAME, desc, target)
    except A.AdapterError as e:
        return die(f"posting the status failed: {e}")
    print(f"posted {state} on {repo}@{sha[:8]}")
    # Action journal: who posted it and what it was before (revert-plan sets it back).
    rec = [sys.executable, "-I", str(HERE.parent / "journal" / "journal.py"), "record",
           "--agent", os.environ.get("AGENT_NAME") or "post-commit-status", "--system", g.name,
           "--action", "commit_status", "--host", key, "--project-id", repo, "--target-type", "commit",
           "--target-id", sha, "--target-key", f"{repo}@{sha[:10]}", "--reversible", "auto",
           "--summary", f"{NAME} status {prev or 'none'} → {state} on {repo}@{sha[:8]}: {desc[:120]}",
           "--before", json.dumps({"sha": sha, "name": NAME, "state": prev or "canceled"}),
           "--after", json.dumps({"sha": sha, "name": NAME, "state": state})]
    try:
        r = subprocess.run(rec, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise OSError(r.stderr.strip()[:200])
    except (OSError, subprocess.SubprocessError):
        print("journal: could not record this status (posted anyway)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
