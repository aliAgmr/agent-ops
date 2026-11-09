#!/usr/bin/env python3
"""Tests for infra/quality/run_signals.py with fake run logs and transcripts.
Run: python3 tests/run-signals.test.py"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp())
os.environ["AGENT_LOG_ROOT"] = str(T / "logs")
os.environ["CLAUDE_PROJECTS_DIR"] = str(T / "projects")
sys.path.insert(0, str(ROOT / "infra" / "quality"))
import run_signals as R  # noqa: E402

fails = []


def check(cond, label, detail=""):
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  {str(detail)[:400]}"))
    if not cond:
        fails.append(label)


now = time.time()
iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))
old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3 * 86400))


def tool_use(name, inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def result(text, err=True):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": err, "content": text}]}}


def session(sid, lines):
    d = T / "projects" / "-srv-agent-ops-service-triage"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


S1, S2, S3 = ("00000000-0000-0000-0000-00000000000" + str(i) for i in (1, 2, 3))
rework = [tool_use("Skill", {"skill": "triage"}), tool_use("Skill", {"skill": "triage"}),
          *[tool_use("Read", {"file_path": "/srv/agent-ops/routing.yaml"}) for _ in range(3)],
          result("Error calling tool 'workitem': HTTP 429: Too Many Requests"),
          result("Error calling tool 'workitem': HTTP 429: Too Many Requests"),
          result("PreToolUse:Bash hook error: BLOCKED by agent-ops guard: pushing to main/master is not allowed"),
          result("…the skill text quotes BLOCKED by agent-ops guard: and is not a block", err=False)]
session(S1, rework)
session(S2, rework)
session(S3, [tool_use("Read", {"file_path": "/srv/agent-ops/people.yaml"})])
(T / "logs" / "triage").mkdir(parents=True)
runs = [{"agent": "triage", "started": iso, "outcome": "ok", "result": {"session_id": S1, "subtype": "success"}},
        {"agent": "triage", "started": iso, "outcome": "ok", "result": {"session_id": S2, "subtype": "error_max_turns",
         "permission_denials": [{"tool_name": "Bash"}]}},
        {"agent": "triage", "started": iso, "outcome": "failed", "result": {"session_id": S3}},
        {"agent": "triage", "started": iso, "outcome": "ok", "result": {"session_id": "not-a-session"}},
        {"agent": "triage", "started": old, "outcome": "failed", "result": {"session_id": S1}}]
(T / "logs" / "triage" / "x.jsonl").write_text("\n".join(json.dumps(r) for r in runs) + "\n")

out = R.collect(24, ["triage", "analyst"], now)
st = out["by_agent"]["triage"]
check(out["runs"] == 4 and st["runs"] == 4, "only runs inside the window count", out)
check(st.get("skill_reread") == 2 and st.get("reread") == 2 and st.get("repeated_error") == 2,
      "a skill loaded twice, a file read 3 times and a repeated error are counted per run", st)
check(st.get("turn_limit") == 1 and st.get("failed") == 1, "turn limit and failed outcome", st)
check(st.get("blocked") == 2, "guard blocks and permission denials, but not a file quoting the guard", st)
check(out["missing_transcripts"] == 1, "a run without a transcript is counted as missing", out)
top = {(t["signal"], t["detail"]) for t in out["top"]}
check(("reread", "routing.yaml") in top and ("skill_reread", "triage") in top, "details seen in 2+ runs are listed", out["top"])
check(all("Too Many" not in t["detail"] or len(t["detail"]) <= 80 for t in out["top"]), "details are short")
check(out["by_agent"]["analyst"] == {}, "an agent with no runs is empty, not an error", out["by_agent"])

print(f"\n{'all passed' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
