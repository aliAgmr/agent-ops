-- agent: release
-- engine: clickhouse
-- pass: the query returns 0 rows
-- why: example check — the warehouse tables the app reads were refreshed in the last 2 days.
--      Replace or extend with real release checks (the data and backend leads own this folder). Each file: one
--      SELECT, a header with engine + pass condition, and a short "why".
with
  last_write as (
    select table, max(modification_time) as last_modified
    from system.parts
    where true
      and active
      and database = 'analytics'   -- the database your app reads
    group by table
  )
select table, last_modified
from last_write
where last_modified < now() - interval 2 day
order by last_modified
