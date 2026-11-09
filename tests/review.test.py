#!/usr/bin/env python3
"""Tests for the release review helpers: infra/quality/mr_scan.py (what an MR removes, risky
additions) and infra/quality/waivers.py (who may waive what). No network.
Run: uv run --no-project --with pyyaml python tests/review.test.py"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra" / "quality"))
fails = []


def check(cond, label, detail=""):
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  {str(detail)[:500]}"))
    if not cond:
        fails.append(label)


try:
    import yaml  # noqa: F401
except ImportError:
    print("skip: pyyaml not installed (CI has it)")
    sys.exit(0)

TMP = Path(tempfile.mkdtemp())
# A repo clone with a router, so route names get the router's prefix.
(TMP / "repos" / "billing-backend" / "src" / "routers").mkdir(parents=True)
(TMP / "repos" / "billing-backend" / "src" / "routers" / "invoice.router.ts").write_text(
    'export const r = new Elysia({ prefix: "/invoices", name: "router:invoices" })\n')
os.environ["AGENT_REPOS_DIR"] = str(TMP / "repos")
os.environ["AGENT_WAIVERS_FILE"] = str(TMP / "waivers.jsonl")
os.environ["AGENT_BOT_USERNAMES"] = "release-bot"
(TMP / "people.yaml").write_text("people:\n  alex: {code_host_usernames: {main: alex.m, oss: alexm}}\n  sam: {code_host_usernames: {}}\n"
                                 "service:\n  tracker_triage_bot: agents+triage@example.com\n  code_host_bots: {main: agent-bot}\n")
os.environ["AGENT_PEOPLE_FILE"] = str(TMP / "people.yaml")

import mr_scan as M  # noqa: E402
import waivers as W  # noqa: E402

# ---- mr_scan --------------------------------------------------------------------------------------
P = M.load_patterns()
check({"removed_routes", "removed_dto_fields", "removed_permission_checks", "removed_auth",
       "added_sql_interpolation"} <= set(P), "mr-scan: the patterns file loads", list(P))
changes = [
    {"new_path": "src/routers/invoice.router.ts",
     "diff": "@@ -1,9 +1,3 @@\n-\t.get(\n-\t\t\"/overdue\",\n-\t\tasync () => {\n-\t.use(tokenAuthPlugin)\n+\t.post(\"/other\", x)\n"},
    {"new_path": "src/routers/payment.router.ts",
     "diff": "@@\n-\t\thasPermission(user.permissions ?? [], \"invoice:export\"),\n+\t\tok,\n"},
    {"new_path": "src/routers/wallet.router.ts",   # a route moved here from another file: not removed
     "diff": "@@\n-\t.get(\"/moved\", a)\n"},
    {"new_path": "src/routers/wallet2.router.ts",
     "diff": "@@\n+\t.get(\"/moved\", a)\n"},
    {"new_path": "src/dtos/invoice.dto.ts",
     "diff": "@@\n-\tcustomer_id: t.String(),\n-\tamount: t.Number(),\n+\tamount: t.Number({ minimum: 0 }),\n"},
    {"new_path": "src/repositories/x.repository.ts",
     "diff": "@@\n+\tconst a = `select * from ${INVOICES_VIEW} order by rank`;\n"
             "+\tconst q = `select * from t where id = ${query.id}`;\n+\tconst ok = `select {id:UInt32}`;\n"},
]
out = M.scan(changes, P, "billing-backend")
rem, add = out["removed"], out["added"]
check([r["name"] for r in rem.get("removed_routes", [])] == ["GET /invoices/overdue"],
      "mr-scan: a removed route, split over two lines, with the router's prefix; a moved route is not removed", rem)
check([r["name"] for r in rem.get("removed_dto_fields", [])] == ["customer_id"],
      "mr-scan: a removed DTO field; a changed one (removed and added back) is not", rem)
check([r["name"] for r in rem.get("removed_permission_checks", [])] == ["invoice:export"],
      "mr-scan: a removed permission check", rem)
check([r["name"] for r in rem.get("removed_auth", [])] == ["tokenAuthPlugin"], "mr-scan: a removed auth plugin", rem)
check([a["name"] for a in add.get("added_sql_interpolation", [])] == ["where … interpolates query.id"],
      "mr-scan: a request value in a query text; constants and parameters are fine", add)
check(M.main(["bad path!", "x"]) == 2, "mr-scan: bad arguments are refused")
# No network in tests: a repo routing.yaml doesn't know, or a host without a token, is an error.
import io, contextlib  # noqa: E401,E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    M.main(["acme/not-in-routing", "12"])
o = json.loads(buf.getvalue())
check(o["error"] and "routing.yaml" in o["error"] and o["removed"] == {},
      "mr-scan: an unreadable diff is an error, never a clean result", o)
try:
    M.fetch_changes("acme/billing-backend", "4", {"code_hosts": {"main": {"type": "gitlab", "url": "https://gitlab.example.com"}},
                                                  "teams": {"backend": {"repos": [{"host": "main", "path": "acme/billing-backend"}]}}})
    check(False, "mr-scan: no release token is an error")
except RuntimeError as e:
    check("CODEHOST_MAIN_RELEASE_TOKEN" in str(e), "mr-scan: no release token is an error", e)

class FakeHost:  # the adapter's merge_request_diffs is what mr-scan reads
    def __init__(self): self.calls = []
    def merge_request_diffs(self, repo, iid): self.calls.append((repo, iid)); return changes[:1]
import adapters as AD  # noqa: E402
fake, saved = FakeHost(), (AD.codehost, AD.load_routing)
fake.token = "t"
AD.codehost = lambda routing, key, kind="read": fake if (key, kind) == ("oss", "release") else None
AD.load_routing = lambda: {"code_hosts": {"oss": {"type": "github", "url": "https://github.com"}},
                           "teams": {"data": {"repos": [{"host": "oss", "path": "acme/warehouse"}]}}}
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    M.main(["acme/warehouse", "9"])
AD.codehost, AD.load_routing = saved
o = json.loads(buf.getvalue())
check(o["error"] is None and o["files"] == 1 and fake.calls == [("acme/warehouse", 9)],
      "mr-scan: reads the diff through the repo's code host (release token)", o)

# ---- waivers --------------------------------------------------------------------------------------
L = Path(os.environ["AGENT_WAIVERS_FILE"])
good = "the view is rebuilt nightly and the duplicates are dropped before any sum"
a = W.add("g/backend!10", "credit-notes-exact-duplicates", "alex.m", good, L)
check(a["accepted"], "waivers: a person with a specific reason is accepted", a)
check(W.add("g/backend!10", "credit-notes-exact-duplicates", "alex.m", good, L).get("duplicate"),
      "waivers: the same waiver twice is recorded once")
check(not W.add("g/backend!10", "x", "release-bot", good, L)["accepted"], "waivers: a service account can't waive")
check(not W.add("g/backend!10", "x", "agent-bot", good, L)["accepted"], "waivers: people.yaml's bot accounts can't waive")
check("not a person" in W.add("g/backend!10", "x", "someone-else", good, L)["why"], "waivers: someone not in people.yaml can't waive")
check(not W.add("g/backend!10", "x", "alex.m", "false positive", L)["accepted"], "waivers: boilerplate is refused")
check(not W.add("g/backend!10", "x", "alex.m", "fine for now ok", L)["accepted"], "waivers: a short reason is refused")
r = W.add("g/backend!11", "y", "alex.m", good, L)
check(not r["accepted"] and "word for word" in r["why"], "waivers: a reason copied from another MR is refused", r)
for i in range(2):
    W.add("g/backend!10", f"id-{i}", "alex.m", f"case {i}: the export only runs for admins behind the VPN today", L)
r = W.add("g/backend!10", "id-9", "alex.m", "one more specific reason that would otherwise be fine here", L)
check(not r["accepted"] and "too many" in r["why"], "waivers: at most 3 per MR", r)
now = time.time()
for mr in ("g/b!1", "g/b!2", "g/b!3"):
    W.add(mr, "order-line-total-repeated", "alex.m", f"{mr}: split shipments are fixed upstream by the sync job", L, now)
rep = W.repeated(30, 3, L, now)
check([x["id"] for x in rep] == ["order-line-total-repeated"] and len(rep[0]["mrs"]) == 3,
      "waivers: an id waived on 3 MRs is reported for nightly", rep)

print(f"\n{'all passed' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
