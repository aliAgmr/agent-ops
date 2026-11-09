#!/usr/bin/env python3
"""Signs of rework in the service agents' runs, for the nightly report: which agent and which
skill make agents repeat themselves. Counting is done here; the model only writes the summary.

  python3 infra/quality/run_signals.py [--hours 24] [--agents triage,analyst,…]

Reads the run logs (AGENT_LOG_ROOT/<agent>/*.jsonl, as run-agent.sh writes them: default
~/Library/Logs/agent-ops on macOS, /var/log/agents on Linux; one line per run with the Claude
session id) and each run's transcript (Claude Code keeps them in ~/.claude/projects/*/<session>.jsonl).
Signals per run:
  repeated_error   the same tool error twice or more in one run
  reread           the same file read 3+ times (Read, or `git … show <rev>:<path>`)
  skill_reread     a skill loaded or its SKILL.md read twice or more
  blocked          calls the guard blocked or permissions denied
  turn_limit       the run stopped at its turn limit
  failed           the run's outcome wasn't ok
Prints JSON: {"hours", "runs", "by_agent": {agent: {"runs", signal: count}}, "top": [{"agent",
"signal", "detail", "runs"}] (details seen in 2+ runs, most frequent first), "missing_transcripts",
"error"}. Never prints tool inputs or outputs beyond a short detail (a path, a skill, an error's
first words with long tokens blanked). Exit 0 always.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_LOGS = "~/Library/Logs/agent-ops" if sys.platform == "darwin" else "/var/log/agents"   # as run-agent.sh
LOG_ROOT = Path(os.environ.get("AGENT_LOG_ROOT", DEFAULT_LOGS)).expanduser()
PROJECTS = Path(os.environ.get("CLAUDE_PROJECTS_DIR", "~/.claude/projects")).expanduser()
AGENTS = ["triage", "analyst", "release", "nightly", "jenkins-watch"]
TOKENISH = re.compile(r"[A-Za-z0-9_+/=-]{24,}")
SHOW_PATH = re.compile(r"git\s+(?:-C\s+\S+\s+)?show\s+[^\s:]+:(\S+)")


def clean(s: str, n: int = 80) -> str:
    return TOKENISH.sub("…", re.sub(r"\s+", " ", s)).strip()[:n]


def ts(s: str) -> float:
    try:
        return time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (ValueError, TypeError):
        return 0.0


def runs_since(agent: str, since: float) -> List[Dict[str, Any]]:
    out = []
    for p in sorted(glob.glob(str(LOG_ROOT / agent / "*.jsonl"))):
        if os.path.getmtime(p) < since - 86400:
            continue
        for line in Path(p).read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if ts(r.get("started", "")) >= since:
                out.append(r)
    return out


def transcript(session: str) -> Optional[Path]:
    if not session or not re.match(r"^[0-9a-f-]{36}$", session):
        return None
    hits = glob.glob(str(PROJECTS / "*" / f"{session}.jsonl"))
    return Path(hits[0]) if hits else None


def run_signals(path: Path) -> Dict[str, collections.Counter]:
    """{signal: Counter(detail)} for one transcript; a detail counts once per run."""
    errors, reads, skills = collections.Counter(), collections.Counter(), collections.Counter()
    blocked = collections.Counter()
    for line in path.read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "tool_use":
                name, inp = part.get("name", ""), part.get("input") or {}
                if name == "Read" and isinstance(inp, dict) and inp.get("file_path"):
                    fp = str(inp["file_path"])
                    (skills if fp.endswith("SKILL.md") else reads)[fp.split("/agent-ops/")[-1]] += 1
                elif name == "Skill" and isinstance(inp, dict):
                    skills[str(inp.get("skill", ""))] += 1
                elif name == "Bash" and isinstance(inp, dict):
                    for m in SHOW_PATH.finditer(str(inp.get("command", ""))):
                        reads[m.group(1)] += 1
            elif part.get("type") == "tool_result":
                text = part.get("content")
                if isinstance(text, list):
                    text = " ".join(str(x.get("text", "")) for x in text if isinstance(x, dict))
                text = str(text or "")
                if not part.get("is_error"):
                    continue   # (a file or skill that only quotes the guard's message isn't a block)
                if "BLOCKED by agent-ops guard" in text:
                    blocked[clean(text.split("BLOCKED by agent-ops guard:")[-1], 60)] += 1
                else:
                    errors[clean(text)] += 1
    return {"repeated_error": collections.Counter({k: 1 for k, v in errors.items() if v >= 2}),
            "reread": collections.Counter({k: 1 for k, v in reads.items() if v >= 3}),
            "skill_reread": collections.Counter({k: 1 for k, v in skills.items() if v >= 2}),
            "blocked": collections.Counter({k: 1 for k in blocked})}


def collect(hours: int, agents: List[str], now: Optional[float] = None) -> Dict[str, Any]:
    since = (now or time.time()) - hours * 3600
    by_agent: Dict[str, Dict[str, int]] = {}
    details: Dict[tuple, int] = collections.Counter()
    total, missing = 0, 0
    for agent in agents:
        stats = collections.Counter()
        for r in runs_since(agent, since):
            total += 1
            stats["runs"] += 1
            res = r.get("result") if isinstance(r.get("result"), dict) else {}
            if res.get("subtype") == "error_max_turns" or res.get("terminal_reason") == "max_turns":
                stats["turn_limit"] += 1
            if r.get("outcome") not in ("ok", None):
                stats["failed"] += 1
            denials = res.get("permission_denials") or []
            if denials:
                stats["blocked"] += 1
                for dn in denials[:5]:
                    details[(agent, "blocked", clean(f"denied {dn.get('tool_name', '?')}" if isinstance(dn, dict) else "denied", 60))] += 1
            p = transcript(str(res.get("session_id") or r.get("session_id") or ""))
            if not p:
                missing += 1
                continue
            for sig, c in run_signals(p).items():
                if c:
                    if sig != "blocked" or not denials:
                        stats[sig] += 1
                    for detail in c:
                        details[(agent, sig, detail)] += 1
        by_agent[agent] = dict(stats)
    top = [{"agent": a, "signal": s, "detail": d, "runs": n}
           for (a, s, d), n in sorted(details.items(), key=lambda x: -x[1]) if n >= 2][:15]
    return {"hours": hours, "runs": total, "by_agent": by_agent, "top": top,
            "missing_transcripts": missing, "error": None}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="run_signals", description=__doc__.split("\n")[0])
    ap.add_argument("--hours", type=int, default=24)
    ap.add_argument("--agents", default=",".join(AGENTS))
    a = ap.parse_args(argv)
    try:
        out = collect(a.hours, [x for x in a.agents.split(",") if x])
    except Exception as e:  # noqa: BLE001 - nightly reports it instead of failing the pass
        out = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
