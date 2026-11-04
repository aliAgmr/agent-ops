-- Read-only reporting schema `bi` over Plane's database, for Metabase (README.md next to this file).
-- Run by apply.sh as Plane's database owner; idempotent (views are replaced, the role is kept).
-- Expects psql variable :ro_password (set on stdin by apply.sh, never on a command line).
--
-- The views hide Plane's internals: soft-deleted rows (deleted_at), drafts, Django foreign-key
-- column names, and secrets in `users`. A Plane upgrade that renames a column breaks a view here,
-- not every dashboard: re-run apply.sh after upgrading and fix this file if it fails.

\set ON_ERROR_STOP on

-- 1. The role Metabase logs in as: no write rights anywhere, only SELECT on `bi`.
SELECT 'CREATE ROLE metabase_ro LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'metabase_ro') \gexec
ALTER ROLE metabase_ro WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
  PASSWORD :'ro_password' CONNECTION LIMIT 10;
ALTER ROLE metabase_ro SET default_transaction_read_only = on;
ALTER ROLE metabase_ro SET statement_timeout = '60s';
ALTER ROLE metabase_ro SET search_path = bi;
REVOKE ALL ON SCHEMA public FROM metabase_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM metabase_ro;
GRANT CONNECT ON DATABASE plane TO metabase_ro;

CREATE SCHEMA IF NOT EXISTS bi;
GRANT USAGE ON SCHEMA bi TO metabase_ro;

-- 2. Views. Times are UTC timestamps; Metabase shows them in its report timezone.

CREATE OR REPLACE VIEW bi.projects AS
SELECT p.id AS project_id, p.identifier AS project, p.name AS project_name, p.created_at
FROM projects p
WHERE p.deleted_at IS NULL;

-- People, without anything sensitive (no password, tokens, phone or login history).
CREATE OR REPLACE VIEW bi.people AS
SELECT u.id AS user_id, u.email, u.display_name,
       nullif(trim(concat_ws(' ', u.first_name, u.last_name)), '') AS full_name,
       u.is_bot, u.is_active
FROM users u;

-- One row per open or closed task (not deleted, not a draft).
CREATE OR REPLACE VIEW bi.tasks AS
WITH started AS (            -- first move into a "started" state (In Progress, In Review, …)
  SELECT a.issue_id, min(a.created_at) AS started_at
  FROM issue_activities a JOIN states s ON s.id = a.new_identifier
  WHERE a.field = 'state' AND a.deleted_at IS NULL AND s."group" = 'started'
  GROUP BY a.issue_id
),
lbl AS (
  SELECT il.issue_id,
         string_agg(l.name, ', ' ORDER BY l.name) AS labels,
         min(l.name) FILTER (WHERE l.name LIKE 'team:%') AS team,
         min(l.name) FILTER (WHERE l.name LIKE 'source:%') AS source
  FROM issue_labels il JOIN labels l ON l.id = il.label_id AND l.deleted_at IS NULL
  WHERE il.deleted_at IS NULL
  GROUP BY il.issue_id
),
asg AS (
  SELECT ia.issue_id,
         string_agg(coalesce(nullif(u.display_name, ''), u.email), ', ' ORDER BY u.email) AS assignees,
         count(*) AS assignee_count
  FROM issue_assignees ia JOIN users u ON u.id = ia.assignee_id
  WHERE ia.deleted_at IS NULL
  GROUP BY ia.issue_id
)
SELECT i.id AS task_id,
       p.identifier || '-' || i.sequence_id AS task_key,
       p.identifier AS project,
       i.name AS title,
       st.name AS state,
       st."group" AS state_group,
       i.priority,
       replace(lbl.team, 'team:', '') AS team,
       replace(lbl.source, 'source:', '') AS source,
       lbl.labels,
       asg.assignees,
       coalesce(asg.assignee_count, 0) AS assignee_count,
       pp.identifier || '-' || par.sequence_id AS parent_key,
       cu.email AS created_by,
       i.created_at,
       i.updated_at,
       started.started_at,
       i.completed_at,
       i.start_date,
       i.target_date,
       i.archived_at,
       (i.target_date < current_date AND st."group" NOT IN ('completed', 'cancelled')) AS is_overdue,
       round((extract(epoch FROM (i.completed_at - i.created_at)) / 86400)::numeric, 1) AS lead_time_days,
       round((extract(epoch FROM (i.completed_at - started.started_at)) / 86400)::numeric, 1) AS cycle_time_days,
       round((extract(epoch FROM (coalesce(i.completed_at, now()) - i.created_at)) / 86400)::numeric, 1) AS age_days,
       i.external_source,
       left(i.description_stripped, 2000) AS description_text,
       '__PLANE_WEB_URL__/__WORKSPACE__/browse/' || p.identifier || '-' || i.sequence_id || '/' AS url
FROM issues i
JOIN projects p ON p.id = i.project_id AND p.deleted_at IS NULL
LEFT JOIN states st ON st.id = i.state_id
LEFT JOIN issues par ON par.id = i.parent_id
LEFT JOIN projects pp ON pp.id = par.project_id
LEFT JOIN users cu ON cu.id = i.created_by_id
LEFT JOIN started ON started.issue_id = i.id
LEFT JOIN lbl ON lbl.issue_id = i.id
LEFT JOIN asg ON asg.issue_id = i.id
WHERE i.deleted_at IS NULL AND NOT i.is_draft;

-- One row per (task, label) and per (task, assignee): for filters and breakdowns.
CREATE OR REPLACE VIEW bi.task_labels AS
SELECT t.task_id, t.task_key, t.project, l.name AS label
FROM bi.tasks t
JOIN issue_labels il ON il.issue_id = t.task_id AND il.deleted_at IS NULL
JOIN labels l ON l.id = il.label_id AND l.deleted_at IS NULL;

CREATE OR REPLACE VIEW bi.task_assignees AS
SELECT t.task_id, t.task_key, t.project, u.email AS assignee_email,
       coalesce(nullif(u.display_name, ''), u.email) AS assignee
FROM bi.tasks t
JOIN issue_assignees ia ON ia.issue_id = t.task_id AND ia.deleted_at IS NULL
JOIN users u ON u.id = ia.assignee_id;

-- Every state move: who moved which task from where to where, when. For flow / cycle-time charts.
CREATE OR REPLACE VIEW bi.state_changes AS
SELECT a.issue_id AS task_id, t.task_key, t.project,
       a.old_value AS from_state, so."group" AS from_group,
       a.new_value AS to_state, sn."group" AS to_group,
       u.email AS actor_email, coalesce(nullif(u.display_name, ''), u.email) AS actor,
       (u.email LIKE '%bot%') AS by_bot,
       a.created_at AS changed_at
FROM issue_activities a
JOIN bi.tasks t ON t.task_id = a.issue_id
LEFT JOIN states so ON so.id = a.old_identifier
LEFT JOIN states sn ON sn.id = a.new_identifier
LEFT JOIN users u ON u.id = a.actor_id
WHERE a.field = 'state' AND a.deleted_at IS NULL;

-- Every recorded change (all fields), for anything the views above don't cover.
CREATE OR REPLACE VIEW bi.task_activity AS
SELECT a.issue_id AS task_id, t.task_key, t.project, a.verb, a.field,
       left(a.old_value, 500) AS old_value, left(a.new_value, 500) AS new_value,
       u.email AS actor_email, a.created_at
FROM issue_activities a
JOIN bi.tasks t ON t.task_id = a.issue_id
LEFT JOIN users u ON u.id = a.actor_id
WHERE a.deleted_at IS NULL;

CREATE OR REPLACE VIEW bi.comments AS
SELECT c.id AS comment_id, c.issue_id AS task_id, t.task_key, t.project,
       u.email AS author_email, coalesce(nullif(u.display_name, ''), u.email) AS author,
       (c.comment_stripped LIKE '[agent:%') AS by_agent,
       substring(c.comment_stripped FROM '^\[agent:([^\]]+)\]') AS agent_tag,
       c.created_at, c.edited_at,
       left(c.comment_stripped, 2000) AS comment_text
FROM issue_comments c
JOIN bi.tasks t ON t.task_id = c.issue_id
LEFT JOIN users u ON u.id = c.actor_id
WHERE c.deleted_at IS NULL;

CREATE OR REPLACE VIEW bi.cycles AS
SELECT cy.id AS cycle_id, p.identifier AS project, cy.name, cy.start_date, cy.end_date, cy.archived_at
FROM cycles cy JOIN projects p ON p.id = cy.project_id
WHERE cy.deleted_at IS NULL;

CREATE OR REPLACE VIEW bi.cycle_tasks AS
SELECT ci.cycle_id, t.task_id, t.task_key, t.project
FROM cycle_issues ci JOIN bi.tasks t ON t.task_id = ci.issue_id
WHERE ci.deleted_at IS NULL;

GRANT SELECT ON ALL TABLES IN SCHEMA bi TO metabase_ro;

-- 3. Report what Metabase will see.
SELECT 'bi.' || table_name AS view, (xpath('/row/c/text()',
       query_to_xml(format('select count(*) as c from bi.%I', table_name), false, true, '')))[1]::text::int AS rows
FROM information_schema.views WHERE table_schema = 'bi' ORDER BY 1;
