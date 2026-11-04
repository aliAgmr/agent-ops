#!/usr/bin/env python3
"""Tests for infra/quality/claims.py (the unsourced-claim rate). Run: python3 tests/quality.test.py"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "infra" / "quality"))
import claims as Q  # noqa: E402

fails = []


def check(cond, label, detail=""):
    print(("ok   " if cond else "FAIL ") + label + ("" if cond else f"  {str(detail)[:500]}"))
    if not cond:
        fails.append(label)


# An analysis as the analyst writes it (HTML in the item's description), with Triage notes after it.
DESC = """<h2>Goal</h2><p>Make the balance number right on the billing page for every customer.</p>
<h2>Analysis — bug ([agent:analyst] 2026-11-04)</h2>
<p>Summary: the billing total double counts split payments since the BIL-39 change; recommend fixing the join.</p>
<h3>Reproduced: yes</h3>
<pre>select count() from analytics.payments_ledger where split_id is not null</pre>
<ul><li>The ledger has 4,812 split rows counted twice for 37 customers in October.</li>
<li>Customer 1442 shows 1,200,000 USD instead of the expected 600,000 USD.</li></ul>
<h3>Where it happens</h3>
<ol><li>The billing page reads the total from the API in src/pages/billing/index.tsx:88 and shows it as is.</li>
<li>The service sums both ledgers without excluding split rows, which is where the doubling happens.</li></ol>
<h3>When it started</h3>
<p>The first doubled row is from 2 Oct, the day MR !212 (commit 9f3ab21c) was merged and deployed.</p>
<h3>Root cause</h3>
<ol><li>The join in payments_ledger.sql:41 matches each split payment twice — confirmed.</li>
<li>Maybe the frontend also adds the pending amount on top of the total shown to the customer — possible.</li></ol>
<h3>Fix options</h3>
<table><tr><th>Option</th><th>What changes</th></tr><tr><td>A</td><td>Fix the join so each split payment matches once only</td></tr></table>
<h3>Questions</h3><ol><li>Should pending split payments count in the billing total at all for customers? — for Kenji</li></ol>
<h3>Sources</h3><ul><li>All queries above; artifact "Acme Billing | Data : Lineage".</li></ul>
<h2>Triage notes ([agent:triage] 2026-11-03)</h2><ul><li>Team backend, effort m, routed after the analysis.</li></ul>"""

sec = Q.analysis_section(DESC)
check(sec.startswith("<h2>Analysis") and "Triage notes" not in sec and "Goal" not in sec,
      "analysis_section: only the ## Analysis part", sec[:80])
m = Q.measure(sec)
check(m["claims"] == 7, "only the fact parts count (not Summary, Fix options, Questions, Sources)", m)
check(m["unsourced"] == 2 and m["rate"] == round(2 / 7, 3), "two claims cite nothing", m)
ex = " ".join(m["examples"])
check("sums both ledgers" in ex and "frontend also adds" in ex, "the unsourced ones are named", m["examples"])
check("4,812 split rows" not in ex, "numbers in a part with a query block count as sourced")

# agent-mcp answers: markdown, every part counts, the Sources list doesn't
ANS = """The backend computes the billing summary in one service.

- `GET /invoices/summary` is built in src/services/internal-services/invoices.service.ts:120.
- Customers are grouped by plan and sorted by outstanding balance, largest first.
- The numbers come from analytics.v_revenue_daily, refreshed every four hours by the pipeline.

## Sources
- src/services/internal-services/invoices.service.ts:120
"""
a = Q.measure(ANS, all_parts=True)
check(a["claims"] == 4 and a["unsourced"] == 3, "answers: claims in the body, sources list excluded", a)

check(Q.measure("")["claims"] == 0 and Q.measure("")["rate"] is None, "empty text: no claims, no rate")
r = subprocess.run([sys.executable, "-I", str(ROOT / "infra/quality/claims.py"), "-", "--all"], input=ANS,
                   capture_output=True, text=True)
check(r.returncode == 0 and json.loads(r.stdout)["unsourced"] == 3, "CLI on stdin", r.stderr)


# the item is read through the tracker adapter (a fake one here)
class FakeTracker:
    def get_item_by_key(self, key):
        return {"id": "w1", "key": key, "description_html": DESC}


check(Q.measure(Q.analysis_section(Q.fetch_item("bil-7", FakeTracker())))["claims"] == 7,
      "fetch_item: the analysis read through the tracker adapter")

# other templates: the fact parts are configurable
OTHER = "<h2>Analysis</h2><h3>Findings</h3><ul><li>The nightly job skips customers created after midnight UTC.</li></ul>" \
        "<h3>Notes</h3><ul><li>This paragraph is commentary and must not be counted as a claim at all.</li></ul>"
r = subprocess.run([sys.executable, "-I", str(ROOT / "infra/quality/claims.py"), "-"], input=OTHER, capture_output=True, text=True,
                   env={"AGENT_CLAIMS_FACT_PARTS": "findings", "PATH": "/usr/bin:/bin"})
check(r.returncode == 0 and json.loads(r.stdout)["claims"] == 1, "AGENT_CLAIMS_FACT_PARTS: your template's fact headings", r.stdout + r.stderr)

print(f"\n{'all passed' if not fails else str(len(fails)) + ' FAILED: ' + ', '.join(fails)}")
sys.exit(1 if fails else 0)
