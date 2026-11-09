#!/usr/bin/env python3
"""Offline tests for infra/lineage/lineage.py (a tiny fake warehouse/backend/frontend tree, fake
ClickHouse views, configured sources, a plugin scanner) and infra/quality/traps.py (a fake query
runner). Run: python3 tests/lineage.test.py (traps needs pyyaml: CI has it; skipped otherwise)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra" / "lineage"))
sys.path.insert(0, str(ROOT / "infra" / "journal"))
import lineage as L  # noqa: E402

T = Path(tempfile.mkdtemp(prefix="lineage-test-"))
fails: list = []


def check(cond, label, detail=""):
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  {str(detail)[:500]}"))
    if not cond:
        fails.append(label)


def w(path: str, text: str) -> None:
    p = T / "repos" / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# ---- a tiny example tree (the example team's repos) -------------------------------------------
WH = "warehouse/models"
w(f"{WH}/billing/payments_ledger.sql",
  "-- builds the ledger\nCREATE OR REPLACE TABLE analytics.payments_ledger_stage ENGINE = MergeTree AS\n"
  "with x as (select 1) select * from raw.invoices p left join `raw`.`customers` s on 1 "
  "join x on 1 join system.parts sp on 1\n")
w(f"{WH}/revenue_daily_base.sql", "select * from analytics.payments_ledger_stage_swap final\n")
w(f"{WH}/zz_retired_thing.sql", "create table analytics.gone as select * from raw.invoices\n")

BE = "billing-backend/src"
w(f"{BE}/routers/invoices.router.ts", '''export const invoicesRouter = new Elysia({
\tprefix: "/invoices",
})
\t.get(
\t\t"/summary",
\t\tasync ({ query, user }) => {
\t\t\tconst result = await InvoicesController.getInstance().getSummary(user.id, query);
\t\t},
\t)
\t.get("/other/:id", async () => OtherController.getInstance().getOther());
''')
w(f"{BE}/controllers/invoices.controller.ts", '''export class InvoicesController {
\tprivate readonly summary = new InvoicesService();
\tasync getSummary(userId, query) {
\t\treturn this.summary.getSummary(userId, query);
\t}
}
''')
w(f"{BE}/services/invoices.service.ts", '''export class InvoicesService {
\tprivate readonly repository = new InvoicesRepository();
\tprivate readonly unused = new OtherRepository();
\tasync getSummary(userId, query) {
\t\treturn this.build(query);
\t}
\tprivate async build(query) {
\t\treturn this.repository.revenue(query);
\t}
}
''')
w(f"{BE}/repositories/clickhouse/invoices.repository.ts", '''const VIEW_CALL = `analytics.v_revenue_daily(`;
export class InvoicesRepository {
\tasync revenue(q) {
\t\treturn ClickHouseClient.query(`select * from ${VIEW_CALL}{a:String})`);
\t}
\tasync other() {
\t\treturn ClickHouseClient.query(`select * from analytics.v_unrelated`);
\t}
}
''')
w(f"{BE}/repositories/clickhouse/other.repository.ts", '''export class OtherRepository {
\tasync x() { return ClickHouseClient.query(`select * from analytics.v_other`); }
}
''')

FE = "billing-frontend/src"
w(f"{FE}/App.tsx", '''import BillingPage from "@/pages/finance/billing";
const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  { path: "revenue", element: <BillingPage /> },
  { path: "revenue/admin", element: <AdminBillingRoute /> },
]);
''')
w(f"{FE}/routes/wrappers.tsx", '''import AdminBilling from "@/pages/finance/admin";
export function AdminBillingRoute() {
  return <AdminBilling />;
}
''')
w(f"{FE}/pages/finance/billing/index.tsx", 'import { getSummary } from "./api";\n')
w(f"{FE}/pages/finance/billing/api/index.ts",
  "export const getSummary = (q) =>\n  apiRequest<Summary>(\n    `/invoices/summary?${q}`,\n  );\n")
w(f"{FE}/pages/finance/admin/index.tsx", 'import { getOther } from "./api";\n')
w(f"{FE}/pages/finance/admin/api/index.ts", "export const getOther = (id) => apiRequest(`/invoices/other/${id}`);\n")

HAND = T / "lineage.yaml"
HAND.write_text('''metrics:
  - id: mrr
    name: Monthly recurring revenue
    from: [analytics.v_revenue_daily]
    shown_on: [/revenue]
    owner: kenji
  - id: ghost
    name: Gone
    from: [analytics.v_never_existed]
pages:
  - path: /revenue
    name: Revenue dashboard  # trailing comment
owners:
  - match: "/*"
    owner: noor
''')
VIEWS = [{"database": "analytics", "name": "v_revenue_daily", "engine": "View",
          "as_select": "WITH a AS (select 1) SELECT * FROM analytics.payments_ledger l JOIN analytics.revenue_daily_base d ON 1 JOIN a ON 1"},
         {"database": "analytics", "name": "v_unrelated", "engine": "View", "as_select": "select * from raw.invoices"}]
SOURCES = [
    {"type": "react_router", "repo": "billing-frontend", "app": "src/App.tsx", "route_files": ["src/routes/wrappers.tsx"]},
    {"type": "sql_dir", "repo": "warehouse", "path": "models", "default_db": "analytics", "skip": ["zz_*"], "name": "warehouse"},
    {"type": "clickhouse_views", "databases": ["analytics"]},
    {"type": "elysia_routes", "repo": "billing-backend", "databases": ["analytics"]},
    {"type": "nope_scanner"},
]

g, notes = L.build(T / "repos", HAND, env={}, views=VIEWS, sources=SOURCES)
E = {(s, d) for s, d, _ in g.edges}

# warehouse (sql_dir)
check(("job:warehouse:payments_ledger", "table:analytics.payments_ledger") in E, "sql_dir: a CREATE target is the table (_stage → production)")
check(("table:raw.invoices", "table:analytics.payments_ledger") in E and ("table:raw.customers", "table:analytics.payments_ledger") in E,
      "sql_dir: db-qualified FROM/JOIN sources, backticks too")
check(not any(s.startswith("table:system.") or s == "table:x" for s, _ in E), "sql_dir: CTEs and system tables are not sources")
check(("table:analytics.payments_ledger", "table:analytics.revenue_daily_base") in E,
      "sql_dir: a bare SELECT file builds <default_db>.<file> (_stage_x → production)")
check("table:analytics.gone" not in g.nodes, "sql_dir: skipped files (skip globs)")
# views
check(("table:analytics.payments_ledger", "table:analytics.v_revenue_daily") in E and ("table:analytics.revenue_daily_base", "table:analytics.v_revenue_daily") in E,
      "clickhouse_views: sources from as_select")
# backend
r = "route:GET /invoices/summary"
check(r in g.nodes and "route:GET /invoices/other/:id" in g.nodes, "elysia_routes: prefix + method path, both route forms")
check(("table:analytics.v_revenue_daily", r) in E, "elysia_routes: route → controller → service → private method → repository → view (const)")
check(("table:analytics.v_unrelated", r) not in E and ("table:analytics.v_other", r) not in E,
      "elysia_routes: only the views this route's methods reach, not every view of the classes")
# frontend
check((r, "page:/revenue") in E, "react_router: page → api path literal (template, query string) → backend route")
check(("route:GET /invoices/other/:id", "page:/revenue/admin") in E, "react_router: a wrapper component in route_files, a ${id} path")
check(not any(d == "page:/login" for _, d in E), "react_router: pages without an api call have no inputs")
check(any("nope_scanner" in n for n in notes), "sources: an unknown type is reported, the rest still scan", notes)
# by hand
check(("table:analytics.v_revenue_daily", "metric:mrr") in E and ("metric:mrr", "page:/revenue") in E,
      "by hand: metric from views, shown on pages")
check(g.nodes["page:/revenue"].get("name") == "Revenue dashboard" and g.nodes["page:/revenue"].get("owner") == "noor",
      "by hand: page names (comment stripped) and owners by glob")
check(any("v_never_existed" in n for n in notes), "check: a hand-file name that doesn't exist is reported", notes)

# queries
up = g.summary(g.walk(g.resolve("/revenue"), "up"))
check({"analytics.payments_ledger", "raw.invoices", "analytics.v_revenue_daily"} <= set(up.get("table", [])), "upstream of a page reaches the warehouse", up)
down = g.summary(g.walk(g.resolve("invoices"), "down"))
check("/revenue (Revenue dashboard)" in down.get("page", []) and any("mrr" in m for m in down.get("metric", [])),
      "impact of a table (name without database) reaches pages and metrics", down)
check(g.resolve("GET /invoices/summary") == [r] and g.resolve("/invoices/summary") == [r], "resolve: route with or without method")
nodes = L.files_to_nodes(g, ["src/repositories/clickhouse/invoices.repository.ts", "models/billing/payments_ledger.sql"])
check("job:warehouse:payments_ledger" in nodes and any(n.endswith("invoices.repository.ts") for n in nodes), "files: changed paths → nodes", nodes)
terms = L.upstream_terms(g, "/revenue")
check("v_revenue_daily" in terms and "payments_ledger" in terms, "upstream_terms for suspects: table names without database", terms)

# a plugin scanner in infra/lineage/scanners/ (TYPE, STAGE, scan)
plug_dir = T / "scanners"
plug_dir.mkdir()
(plug_dir / "dbt_like.py").write_text('''TYPE = "dbt_like"
STAGE = 0
def scan(g, cfg, ctx):
    g.node("table:analytics.from_plugin", layer="warehouse")
    g.edge("table:analytics.from_plugin", "table:" + cfg["feeds"], "feeds", "plugin")
''')
check(L.load_plugin_scanners(plug_dir) == ["dbt_like"], "plugin scanner registered from its module")
g3, _ = L.build(T / "repos", HAND, env={}, views=VIEWS, sources=[*SOURCES[:4], {"type": "dbt_like", "feeds": "analytics.payments_ledger"}])
check(("table:analytics.from_plugin", "table:analytics.payments_ledger") in {(s, d) for s, d, _ in g3.edges},
      "plugin scanner: its nodes and edges join the graph")

# the shipped example config loads and names only built-in scanners
srcs, f = L.load_sources(ROOT / "infra" / "lineage" / "sources.example.yaml")
check(srcs and all(s.get("type") in L.SCANNERS for s in srcs) and isinstance(srcs[-1].get("api_calls"), list),
      "sources.example.yaml: loads, every type has a scanner, lists parse", srcs)

# cache round trip
L.CACHE = T / "cache.json"
L.save(g, notes)
g2 = L.Graph.from_json(json.loads(L.CACHE.read_text()))
check(len(g2.edges) == len(g.edges) and g2.nodes.keys() == g.nodes.keys(), "cache: save and load give the same graph")

# traps runner
try:
    import yaml  # noqa: F401
    sys.path.insert(0, str(ROOT / "infra" / "quality"))
    import traps as TR
    checks = TR.load_checks()
    check(len(checks) >= 3 and all(c["sql"].lstrip().startswith("--") for c in checks),
          "traps: the checks file loads; every query starts with its attribution comment")
    check(all(c["action"] in ("block", "notify") and c["severity"] in ("high", "medium", "low") for c in checks),
          "traps: every check has a valid action and severity")
    sample = [{"id": "a", "sql": "1", "expect": "== 0", "tables": ["analytics.x"], "action": "block"},
              {"id": "b", "sql": "2", "expect": "< 0.5", "tables": ["analytics.y"], "action": "notify"},
              {"id": "c", "sql": "3", "expect": "== 0", "tables": ["analytics.z"], "action": "block"},
              {"id": "d", "sql": "4", "expect": "<= 10", "tables": [], "action": "block"}]
    answers = {"1": "3", "2": "0.7", "3": RuntimeError("Code: 241. Memory limit exceeded"), "4": "10"}

    def fake(sql):
        a = answers[sql]
        if isinstance(a, Exception):
            raise a
        return a
    out = TR.run(sample, env={}, runner=fake)
    st = {r["id"]: r["status"] for r in out["results"]}
    check(st == {"a": "fail", "b": "fail", "c": "unreadable", "d": "pass"}, "traps: pass / fail / unreadable", st)
    check(out["blocking"] == ["a"] and out["notify"] == ["b"] and out["unreadable"] == ["c"],
          "traps: a failing block check blocks, notify doesn't, an error is unreadable (never a pass)", out)
    # Baseline: known violations no worse than their entry don't block; worse ones do; a check
    # that passes again while it has an entry is listed as stale.
    out = TR.run(sample, env={}, runner=fake, baseline={"a": 3, "b": 0.6, "d": 12})
    st = {r["id"]: r["status"] for r in out["results"]}
    check(st["a"] == "baseline" and out["blocking"] == [] and "a" in out["baselined"],
          "traps: a violation at its baseline value is known, not blocking", out)
    check(st["b"] == "fail" and out["notify"] == ["b"], "traps: worse than the baseline is a normal fail", st)
    check(out["stale_baseline"] == ["d"], "traps: a passing check with a baseline entry is stale", out)
    check(TR.load_baseline() == {} or isinstance(TR.load_baseline(), dict), "traps: the baseline file loads")
except ImportError:
    print("skip traps runner tests: pyyaml not installed (CI has it)")

print(f"\n{'all passed' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
