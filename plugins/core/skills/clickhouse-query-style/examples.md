# ClickHouse query examples (house style)

Short templates only, on an example billing warehouse (`raw.*` synced from the application
database, `analytics.*` modelled tables). Prefer the simplest template that fits. Every query
starts with the `-- agent:` attribution line.

## 1. MergeTree table from CTEs

```sql
-- agent: kenji (claude-code)
create or replace table analytics.revenue_customer_daily
engine = MergeTree()
order by
        (
         day
        ,customer_id
        ) as

with revenue_90d as
(
select
         cal.day as day
        ,cal.customer_id as customer_id
        ,sum(inv.amount) as revenue_90d
        ,sum(inv.amount) filter (where inv.invoice_date = cal.day) as revenue_on_day
from analytics.calendar_customer_daily cal
left join analytics.invoices_daily inv on
                                    inv.customer_id = cal.customer_id
                                    and inv.invoice_date <= cal.day
                                    and inv.invoice_date > cal.day - interval 90 day
group by
         cal.day
        ,cal.customer_id
)

select *
from revenue_90d
```

## 2. View

```sql
-- agent: kenji (claude-code)
create or replace view analytics.v_open_invoices as

select
         i.id as invoice_id
        ,i.customer_id as customer_id
        ,i.issued_at as issued_at
        ,i.amount - i.paid_amount as open_amount
        ,i.status as status
        ,coalesce(i.channel, 'web') as channel
from raw.invoices i FINAL
join raw.customers c FINAL on c.id = i.customer_id
where
        true
        and i.is_deleted = 0
        and c.is_deleted = 0
        and (i.amount - i.paid_amount) > 0
        and i.issued_at::date >= today() - interval 365 day
        and i.status in ('issued', 'overdue')
```

## 3. Calendar × dimension

```sql
-- agent: kenji (claude-code)
create or replace table analytics.calendar_customer_daily
engine = MergeTree()
order by
        (
         day
        ,customer_id
        ) as

with calendar_daily as
(
select
        toDate(today() - INTERVAL number DAY) as day
from numbers(365)
)

select
         day
        ,customer_id
        ,region
        ,plan
from calendar_daily cd
cross join analytics.active_customers
```

## 4. Union of eligibility lists, then enrich

```sql
-- agent: kenji (claude-code)
with invoiced_customer_list as
(
select distinct customer_id as customer_id
from analytics.invoices_daily
where
        true
        and invoice_date >= today() - interval 90 day
)

,subscribed_customer_list as
(
select
        distinct s.customer_id as customer_id
from raw.subscriptions s FINAL
where
        true
        and s.is_deleted = 0
        and s.status = 'active'
)

,pre_active_customer_list as
(
select customer_id from invoiced_customer_list
union all
select customer_id from subscribed_customer_list
)

,active_customer_list as
(
select
        distinct customer_id as customer_id
from pre_active_customer_list
)

select
         assumeNotNull(c.id) as customer_id
        ,assumeNotNull(c.region) as region
        ,assumeNotNull(c.plan) as plan
from active_customer_list acl
join raw.customers c FINAL on c.id = acl.customer_id
where
        true
        and c.is_deleted = 0
```

## 5. Filter aggregates

```sql
-- agent: kenji (claude-code)
with payment_split as
(
select
         p.customer_id as customer_id
        ,sum(coalesce(p.amount, 0)) filter (
                where case
                        when p.method = 'card' and p.currency = 'EUR' then true
                        when p.method = 'bank_transfer' and p.settled = 1 then true
                        else false
                end
        ) as settled_amount
        ,sum(coalesce(p.amount, 0)) as total_amount
from raw.payments p FINAL
where
        true
        and p.is_deleted = 0
group by
         p.customer_id
)

select *
from payment_split
```

## 6. Chunked query placeholders

```sql
where
        true
        and inv.customer_id >= ($COUNTER_MIN) and inv.customer_id < ($COUNTER_MAX)
```
