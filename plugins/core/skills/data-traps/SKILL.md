---
name: data-traps
description: Known traps in your team's warehouse that silently produce wrong numbers — row grain, totals repeated on every row, duplicates, paid vs remaining, mixed populations — and the executable checks that catch them. Use before writing or reviewing any warehouse query, view or table over the tables listed here, when a number on a page looks wrong, or when triaging a data/backend task about warehouse figures.
---

# Data traps

Each trap gives plausible-looking but wrong numbers. Check every query against this list. This
file ships with **examples** on a sample billing warehouse (`raw.*`, `analytics.*`): replace them
with your own warehouse's traps, keeping the format. A good trap entry names the table, says what
goes wrong, gives the right way, and, when you measured it, how wrong the naive way was.

## Row grain

1. **`analytics.payments_ledger`** — one row per (invoice, payment instalment). The amount owed on
   an invoice is `sum(instalment_amount)` over all its rows. Never `any()`/`argMax()` on a column
   that is equal across rows: it picks one instalment and understates the total.
2. **`analytics.order_lines_daily`** — one row per (order line × shipment), and `line_total` is the
   **order-line total repeated on every shipment row**. Revenue = `sum(line_total / shipment_count)`
   (or deduplicate to one row per line first). A naive `sum(line_total)` overstates multi-shipment
   orders.

## Duplicates

3. **`analytics.credit_notes` is not deduplicated** — the sync can write the same credit note twice
   with the same `issued_at`. Aggregate to one row per credit note (or per day) **before** joining,
   or the join fans out credits.

## Definitions

4. **Outstanding = `amount - paid_amount`, per invoice.** `paid_amount` alone is cash already
   received (a cash view, not receivables), and an invoice paid more than its amount means the
   payments were double-allocated.
5. **Mixed populations.** `analytics.invoices_daily` holds both direct sales and marketplace
   (third-party) invoices; only direct sales have receivables. Filter `channel = 'direct'` on every
   leg of a receivables metric.

## Checks you can run

`checks.yaml` next to this file turns the traps that show up in the data into read-only SELECTs,
each returning one number with a threshold (`expect`), a `severity` and an `action` (`block` fails a
release that reaches its tables; `notify` files a finding). They run every night
(`infra/bin/traps run`, findings `trap:<id>`) and on every release, for the tables the change's
files reach through lineage (`traps for-files`). By hand: run a check's `sql` through the read-only
ClickHouse MCP, or on the agent host `infra/bin/traps run --tables analytics.payments_ledger`. A
check that can't run is never a pass. When a trap above can be caught in the data, add a check for
it in the same merge request.

To see which tables are behind a page, route or view (and so which traps can apply), use
agent-mcp's `lineage` tool (`direction: "upstream"`).

When you find a new trap, add it here through a merge request to agent-ops so every agent learns it.
