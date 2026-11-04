#!/usr/bin/env python3
"""Runs the executable data-trap checks (plugins/core/skills/data-traps/checks.yaml) against
ClickHouse with the agents' read-only user (CH_RO_*), and says which pass, fail or couldn't run.
Another checks file: AGENT_TRAPS_CHECKS=<path>. Each check is one read-only SELECT returning one
number, with `expect` ("== 0", "< 0.005" …), `severity`, `action` (block | notify) and `tables`.

  traps list                               the checks
  traps run [--ids a,b] [--tables t1,t2]   run all, or those by id / reading these tables
  traps for-files <path> [<path>…]         the checks an MR's changed files reach through lineage
                                           (tables those files read or feed), then run them
Prints JSON: {"results": [{id, value, expect, status: pass|fail|unreadable, action, severity, ms,
why, error}], "blocking": [ids failing with action block], "notify": [...], "unreadable": [...]}.
Exit 0 always (the caller decides); a check that can't run is never a pass.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parents[2]
def default_checks() -> Path:
    """AGENT_TRAPS_CHECKS, else the core plugin's data-traps skill (or its older name)."""
    if os.environ.get("AGENT_TRAPS_CHECKS"):
        return Path(os.environ["AGENT_TRAPS_CHECKS"]).expanduser()
    skills = ROOT / "plugins" / "core" / "skills"
    for name in ("data-traps", "warehouse-traps"):
        if (skills / name / "checks.yaml").is_file():
            return skills / name / "checks.yaml"
    return skills / "data-traps" / "checks.yaml"


CHECKS = default_checks()
EXPECT = re.compile(r"^\s*(==|!=|<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)\s*$")


def load_checks(path: Path = CHECKS) -> List[Dict[str, Any]]:
    data = yaml.safe_load(path.read_text()) or {}
    out = []
    for c in data.get("checks") or []:
        if not c.get("id") or not c.get("sql") or not EXPECT.match(str(c.get("expect", ""))):
            raise SystemExit(f"bad check in {path}: {c.get('id')!r} needs id, sql and expect like '== 0'")
        c["tables"] = [t.lower() for t in c.get("tables") or []]
        out.append(c)
    return out


def meets(value: float, expect: str) -> bool:
    op, n = EXPECT.match(expect).groups()
    n = float(n)
    return {"==": value == n, "!=": value != n, "<=": value <= n, ">=": value >= n, "<": value < n, ">": value > n}[op]


def env_values() -> Dict[str, str]:
    out: Dict[str, str] = {}
    p = Path(os.environ.get("AGENT_OPS_ENV", "~/.config/agent-ops/env")).expanduser()
    if p.is_file():
        for line in p.read_text().splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip("'\"")
    return {**out, **{k: v for k, v in os.environ.items() if k.startswith("CH_RO_") and v}}


def query(sql: str, env: Dict[str, str], timeout: int = 120) -> str:
    host = env.get("CH_RO_HOST")
    if not host or not env.get("CH_RO_PASSWORD"):
        raise RuntimeError("CH_RO_HOST / CH_RO_PASSWORD not set")
    scheme = "https" if env.get("CH_RO_SECURE", "true").lower() == "true" else "http"
    url = f"{scheme}://{host}:{env.get('CH_RO_PORT', '8443')}/?readonly=1&max_execution_time={timeout}&default_format=TabSeparated"
    auth = base64.b64encode(f"{env.get('CH_RO_USER', 'agent_ro')}:{env['CH_RO_PASSWORD']}".encode()).decode()
    req = urllib.request.Request(url, data=sql.encode(), headers={"Authorization": f"Basic {auth}"})
    ctx = ssl.create_default_context()
    if env.get("CH_RO_INSECURE") == "1":
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout + 15, context=ctx) as r:  # noqa: S310 (our warehouse)
            return r.read().decode().strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode("utf-8", "replace").strip().splitlines()[0][:300]) from None


def run(checks: List[Dict[str, Any]], env: Optional[Dict[str, str]] = None, runner=None) -> Dict[str, Any]:
    env = env if env is not None else env_values()
    runner = runner or (lambda sql: query(sql, env))
    results = []
    for c in checks:
        t0 = time.time()
        r = {"id": c["id"], "expect": c["expect"], "action": c.get("action", "notify"),
             "severity": c.get("severity", "medium"), "tables": c["tables"], "why": c.get("why", "")}
        try:
            out = runner(c["sql"])
            value = float(out.split()[0]) if out else None
            if value is None:
                raise RuntimeError("no value returned")
            r.update(value=value, status="pass" if meets(value, c["expect"]) else "fail")
        except Exception as e:  # noqa: BLE001 - every failure to run is "unreadable", never "pass"
            r.update(value=None, status="unreadable", error=str(e)[:300])
        r["ms"] = int((time.time() - t0) * 1000)
        results.append(r)
    return {"results": results,
            "blocking": [r["id"] for r in results if r["status"] == "fail" and r["action"] == "block"],
            "notify": [r["id"] for r in results if r["status"] == "fail" and r["action"] != "block"],
            "unreadable": [r["id"] for r in results if r["status"] == "unreadable"]}


def tables_for_files(paths: List[str]) -> List[str]:
    """Tables the changed files read or feed, through infra/lineage (both directions)."""
    sys.path.insert(0, str(ROOT / "infra" / "lineage"))
    import lineage as L
    g = L.load()
    nodes = L.files_to_nodes(g, paths)
    reached = {r["node"] for d in ("down", "up") for r in g.walk(nodes, d, 6)}
    return sorted(n[6:] for n in reached if n.startswith("table:"))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="traps", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("run"); r.add_argument("--ids"); r.add_argument("--tables")
    f = sub.add_parser("for-files"); f.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    checks = load_checks()
    if a.cmd == "list":
        print(json.dumps([{k: c.get(k) for k in ("id", "trap", "tables", "expect", "severity", "action")} for c in checks], indent=2))
        return 0
    if a.cmd == "for-files":
        tables = set(tables_for_files(a.paths))
        chosen = [c for c in checks if tables & set(c["tables"])]
        out = run(chosen)
        out["tables_reached"] = len(tables)
    else:
        ids = set((a.ids or "").split(",")) - {""}
        tables = {t.strip().lower() for t in (a.tables or "").split(",") if t.strip()}
        chosen = [c for c in checks if (not ids or c["id"] in ids) and (not tables or tables & set(c["tables"]))]
        out = run(chosen)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
