// Code node "Collect DMs" of the plane-notify flow (source of truth; build-flow.sh embeds it).
// Input: one Plane webhook delivery. Output: nothing. It only marks the task as "changed" in the
// workflow's static data; "Flush DMs" (every minute) reads the task's activity and comments from
// Plane's API and sends the batched private messages. See infra/n8n/README.md ("Private messages").
//
// Why only a marker and not the formatted change: Plane sends one webhook per changed field, all
// within a few ms, and n8n runs them as parallel executions that each load the static data at the
// start and write all of it back at the end (last writer wins). Formatted lines would get lost;
// markers for the same task are identical, so losing all but one of them loses nothing. Flush
// also re-scans recently updated tasks, which covers the rare lost marker of another task.

// Plane project id → identifier (filled by build-flow.sh).
const PROJECTS = __PROJECTS__;

const body = $input.first().json.body || {};
const { event, action, data = {}, activity = {} } = body;
if (!['issue', 'issue_comment'].includes(event)) return [];
const issueId = event === 'issue' ? data.id : data.issue;
if (!issueId) return [];

const now = Date.now();
const sd = $getWorkflowStaticData('global');
const dm = JSON.parse(JSON.stringify(sd.dm || {}));
dm.pending = dm.pending || {};
dm.tasks = dm.tasks || {};
dm.deleted = dm.deleted || {};
// A delete may carry only the id; the project then comes from what we remembered about the task.
const project = data.project || (dm.tasks[issueId] || {}).p;
if (!PROJECTS[project]) return [];

const prev = dm.pending[issueId];
dm.pending[issueId] = { p: project, t: prev && prev.t ? prev.t : now };

// Remember title and assignees, so a deleted task (no longer readable in Plane) can still be
// reported to the people who had it.
if (event === 'issue' && data.sequence_id) {
  dm.tasks[issueId] = {
    p: project,
    key: `${PROJECTS[project]}-${data.sequence_id}`,
    title: data.name || '',
    assignees: (data.assignees || []).map((a) => (typeof a === 'string' ? a : a.id)).filter(Boolean),
    u: now,
  };
}
if (event === 'issue' && action === 'deleted') {
  const actor = activity.actor || {};
  dm.deleted[issueId] = { p: project, at: now, actor: actor.id || '' };
}

sd.dm = dm; // re-assign the top-level key so n8n notices the change and saves it
return [];
