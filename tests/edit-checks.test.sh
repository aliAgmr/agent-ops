#!/usr/bin/env bash
# Tests for plugins/core/scripts/edit-checks.sh (advisory checks after a file edit) with the example
# rules in plugins/core/edit-checks.tsv. Run: bash tests/edit-checks.test.sh
set -u
HOOK="$(cd "$(dirname "$0")/.." && pwd)/plugins/core/scripts/edit-checks.sh"
T="$(mktemp -d)"; fail=0
mkdir -p "$T/repo/src/routers" "$T/repo/src/repositories/clickhouse" "$T/repo/views"
run() { printf '{"tool_name":"Edit","tool_input":{"file_path":"%s"}}' "$1" | bash "$HOOK"; }
expect() {  # expect <label> <file> <id or -silent->
  out="$(run "$2")"
  if [ "$3" = "-silent-" ]; then
    [ -z "$out" ] && echo "ok   $1" || { echo "FAIL $1 (expected silence: $out)"; fail=1; }
  else
    printf '%s' "$out" | grep -q '"additionalContext"' && printf '%s' "$out" | grep -q -- "$3" \
      && echo "ok   $1" || { echo "FAIL $1 (expected $3: $out)"; fail=1; }
  fi
}
printf 'select invoice_id, argMax(instalment_amount, updated_at)\nfrom analytics.payments_ledger group by invoice_id\n' > "$T/repo/a.sql"
expect "trap 1: argMax on the payments ledger" "$T/repo/a.sql" trap-ledger-any
printf 'select sum(line_total) from analytics.order_lines_daily\n' > "$T/repo/b.sql"
expect "trap 2: plain sum of the line total" "$T/repo/b.sql" trap-line-total-sum
printf 'select sum(line_total / shipment_count) from analytics.order_lines_daily\n' > "$T/repo/c.sql"
expect "trap 2: divided sum is fine" "$T/repo/c.sql" -silent-
printf 'const q = `select * from analytics.credit_notes c join analytics.invoices i on 1`\n' > "$T/repo/src/repositories/clickhouse/c.repository.ts"
expect "trap 3: credit notes joined without grouping (in a repository)" "$T/repo/src/repositories/clickhouse/c.repository.ts" trap-credit-notes-join
printf 'select sum(amount - paid_amount) from analytics.invoices_daily\n' > "$T/repo/d.sql"
expect "trap 5: receivables without the channel filter" "$T/repo/d.sql" trap-receivables-channel
printf "select sum(amount - paid_amount) from analytics.invoices_daily where channel = 'direct'\n" > "$T/repo/e.sql"
expect "trap 5: with the channel filter is fine" "$T/repo/e.sql" -silent-
printf 'create or replace view analytics.open_invoices as\nselect * from analytics.invoices_daily\n' > "$T/repo/views/open.sql"
expect "a view on select *" "$T/repo/views/open.sql" view-select-star
printf 'return { status: 404, error_message: "Invoice not found" }\n' > "$T/repo/src/routers/i.router.ts"
expect "an error without a code" "$T/repo/src/routers/i.router.ts" error-without-code
printf 'return { status: 404, error: "INVOICE_NOT_FOUND", error_message: "Invoice not found" }\n' > "$T/repo/src/routers/j.router.ts"
expect "an error with a code is fine" "$T/repo/src/routers/j.router.ts" -silent-
printf 'select 1\n' > "$T/repo/ok.sql"
expect "a clean file: silence" "$T/repo/ok.sql" -silent-
expect "a missing file: silence" "$T/repo/nope.sql" -silent-
# The globs must not expand against the current folder (a .sql file in the cwd once broke this).
out="$(cd "$T/repo" && run "$T/repo/a.sql")"
printf '%s' "$out" | grep -q trap-ledger-any && echo "ok   globs are patterns, also with .sql files in the cwd" \
  || { echo "FAIL globs expanded against the cwd: $out"; fail=1; }
for f in a.sql views/open.sql src/routers/i.router.ts; do
  out="$(run "$T/repo/$f")"
  printf '%s' "$out" | python3 -c 'import json,sys; json.loads(sys.stdin.read())' 2>/dev/null \
    && echo "ok   output is valid JSON ($f)" || { echo "FAIL output is not JSON ($f): $out"; fail=1; }
done
rm -rf "$T"
exit $fail
