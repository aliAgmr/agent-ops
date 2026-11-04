---
name: clickhouse-query-style
description: >-
  Writes ClickHouse SQL in one consistent, readable house style: an attribution comment on the
  first line, named CTEs, MergeTree tables / views, leading commas, where true, FINAL,
  assumeNotNull, filter aggregates, simple SQL. Use when the user asks for a ClickHouse query,
  table, view or warehouse SQL, pastes table schemas and wants a query, or has you run anything
  through a ClickHouse MCP server, even a one-line select.
---

# ClickHouse query style

Write ClickHouse SQL that matches one house style, so every query in the warehouse repo and every
query an agent runs looks the same. The user usually pastes **table schemas** and describes the
result they want: use only those schemas plus this guide. Adapt the database names and domain
conventions below to your warehouse (the examples use `raw`, `staging`, `analytics`).

## Attribution line (every query, no exceptions)

The first line of every query you run or write is a comment saying who it is for, so the
warehouse's query log shows which agent and person ran it:

```sql
-- agent: <person or service agent> (<tool>)
```

For example `-- agent: noor (claude-code)` or `-- agent: analyst`. Service agents use their own
name. A team may change the prefix (some log parsers expect `-- Claude user:`); keep whatever your
team set in this skill.

## Workflow

1. Read the schemas and the requested grain / metrics.
2. Prefer existing modelled tables (`analytics.*`) over re-joining raw synced tables when the schema
   set includes them.
3. Build the query with named CTEs; keep each CTE doing one clear job.
4. Output only the SQL (or SQL + a one-line note if a naming choice is ambiguous).

## Output shape

Ask only if unclear; otherwise pick from:

| Kind | Pattern |
|------|---------|
| Materialized table | `create or replace table analytics.<name>` + `engine = MergeTree()` + `order by (...)` + `as` |
| View | `create or replace view analytics.v_<name> as` |
| Ad-hoc / fragment | bare `with ... select ...` (no DDL) when they only ask for a query |

Default database for new tables: `analytics`. Typical sources: `raw.*` (synced from the
application databases), `staging.*`, plus existing `analytics.*`.

## Hard style rules

### Structure

- Use `with cte_name as (...)` then `,next_cte as (...)` — not nested subqueries in FROM when a CTE is clearer.
- Name CTEs in snake_case describing content: `open_invoices`, `daily_payments`, `customer_list`, `final`.
- Final step is usually `select * from <last_cte>` or an explicit column list from the last CTE.
- Put join keys and filters close to the join; keep `group by` aligned with selected non-aggregates.

### Formatting (match this look)

```sql
-- agent: kenji (claude-code)
create or replace table analytics.example_name
engine = MergeTree()
order by
        (
         day
        ,customer_id
        ) as

with some_cte as
(
select
         a.col1 as col1
        ,a.col2 as col2
        ,sum(b.amount) as total_amount
from analytics.some_table a FINAL
left join analytics.other_table b on
                                    b.customer_id = a.customer_id
                                    and b.day = a.day
where
        true
        and a.is_deleted = 0
group by
         a.col1
        ,a.col2
)

select
         assumeNotNull(col1) as col1
        ,assumeNotNull(col2) as col2
        ,total_amount
from some_cte
```

- Leading commas for select / order by / group by lists after the first item.
- Alias every output column: `expr as name`.
- Prefer `where true` + `and ...` so filters are easy to comment out.
- Leave disabled debug filters as `-- and customer_id = ...` comments when useful; do not invent noisy comments.
- `order by` tuple: one column per line, leading commas.

### ClickHouse usage — stay simple

Use the small set of patterns below. **Do not** reach for fancy functions, deep `arrayMap`/`range`
tricks, or window-function heavy solutions unless the request clearly needs them.

| Prefer | Typical use |
|--------|-------------|
| `FINAL` | On Replacing-style sources (e.g. tables synced from an application database) |
| `assumeNotNull(x) as x` | Non-nullable keys in the final projection of MergeTree tables |
| `coalesce(...)` | Null-safe measures |
| `sum(...) filter (where ...)` | Conditional aggregates (split by status, region, date window, etc.) |
| `toDate(...)`, `today()`, `makeDate(y,m,d)` | Dates |
| `interval N day` / `interval N minute` | Windows |
| `::date`, `::Float64` | Simple casts |
| `arrayJoin(...)` | Explode arrays |
| `join ... on true` | Cross-join a one-row CTE (e.g. bounds) |
| `numbers(N)` + `toDate(today() - INTERVAL number DAY)` | Calendar spine |
| `cross join` | Calendar × entity dimension |
| `distinct` in small list CTEs | Build id lists before the main join |

Placeholders, only when a query is clearly meant to be batched or day-parameterized like existing
ones in your repo: `$DAY`, `$COUNTER_MIN` / `$COUNTER_MAX`.

### Domain conventions

Put your warehouse's recurring filters here so every agent applies them the same way, for example:

- Only live rows: `is_deleted = 0`.
- Net quantities: `quantity - returned_quantity`.
- Prefer a calendar table (`analytics.calendar_daily`) + left joins for daily grains instead of
  generating dates ad hoc when one is in scope.

Known data traps (row grain, repeated totals, duplicates…) live in the `data-traps` skill: read it
before writing a query over tables it lists.

## Do / don't

**Do**

- Keep SQL human-readable: short CTEs, obvious names, explicit join conditions.
- Mirror naming of similar existing objects in your warehouse.
- Use `case when ... then ... else ... end` for readable branching.
- Wrap nullable sort keys with `assumeNotNull` when creating MergeTree tables.

**Don't**

- Do not over-nest, over-window, or use advanced ClickHouse functions for problems solvable with joins + `group by` + `filter`.
- Do not invent columns or tables not present in the provided schemas / known pipeline.
- Do not use `SELECT *` from raw wide synced tables in the final output when a tight column list is expected — `select * from <final_cte>` is fine when the CTE already projects the right columns.
- Do not reformat into a different SQL style (trailing-only commas, ORMs, sparkSQL, etc.).

## Minimal examples

**Calendar spine**

```sql
with calendar_daily as
(
select
        toDate(today() - INTERVAL number DAY) as day
from numbers(365)
)
```

**Conditional sum**

```sql
sum(amount) filter (where status = 'paid' and channel in ('web', 'mobile')) as paid_online_amount
```

**Id-list CTE then join**

```sql
,active_customer_list as
(
select
        distinct customer_id as customer_id
from pre_active_customer_list
)
```

For fuller copy-paste templates, see [examples.md](examples.md).
