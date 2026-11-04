// Code node "Flush DMs" of the plane-notify flow (source of truth; build-flow.sh embeds it).
// Runs every minute (Schedule Trigger). Output: one item per message,
//   { kind: 'telegram', chat_id, text }      → "Send DM (Telegram)" (parse_mode HTML)
//   { kind: 'mattermost', channel, text }    → "Send DM (Mattermost)" (only with MM_DM_ENABLED=true)
//
// Rules (infra/n8n/README.md, "Private messages"):
//   - recipients are the task's assignees (plus whoever was just unassigned), never the person who
//     made the change; changes by the agents (triage/release bot) count like anyone else's
//   - reviewers also hear about some changes on tasks that aren't theirs (WATCH below): this is
//     what the group topic used to @mention them for; there is no group post any more
//   - parked tasks, unassigned on purpose, go to their watchers while they sit in their parking
//     state, including the move out of it: nightly findings in Findings go to the project's
//     question owner (PARKED below)
//   - one message per person covering all their changed tasks, sent once their oldest unsent change
//     is 5 minutes old
//   - quiet hours (CONFIG.quiet_from–quiet_to in CONFIG.tz, default 22:00–08:00 UTC): nothing is
//     sent; the first run after them sends what piled up overnight as one digest
//
// What changed comes from Plane itself (the task's activity log + comments), not from the webhook
// payloads: "Collect DMs" only marks tasks in the static data (see dm-collect.js for why). Every run
// also looks at tasks updated in the last 15 minutes, in case a marker was lost.
//
// State (workflow static data, key "dm"; saved only by production runs of the active flow):
//   pending[issueId] = { p: projectId, t: firstSeenMs }          set by Collect DMs
//   tasks[issueId]   = { p, key, title, assignees: [userId] }    set by Collect DMs (for deletes)
//   deleted[issueId] = { p, at, actor }                          set by Collect DMs
//   sent[issueId]    = { base: ms, r: { email: ms }, u: ms }     per-person cursor: sent up to here

// Team settings from routing.yaml / projects.yaml / the env (filled by build-flow.sh):
// { web_url, workspace, tz, quiet_from, quiet_to, roles: { default: {reviewers, questions}, <KEY>: … } }
const CONFIG = __CONFIG__;
const PLANE_URL = CONFIG.web_url;
const WORKSPACE = CONFIG.workspace;
const API = `${__PLANE_API__}/api/v1/workspaces/${WORKSPACE}`;
// Plane project id → identifier (filled by build-flow.sh).
const PROJECTS = __PROJECTS__;
// people.yaml key → { name, email, telegram, mattermost, chat_id } (filled by build-flow.sh).
const PEOPLE = __PEOPLE__;

const MIN = 60 * 1000;
const WINDOW = 5 * MIN;        // batch window, from a person's oldest unsent change
const SCAN = 15 * MIN;         // also check tasks updated this recently (lost markers)
const LEAD = 2 * MIN;          // a marked task's history is read from this long before the marker
const KEEP = 3 * 24 * 60 * MIN; // forget cursors/markers this old
const MAX_LEN = 3900;          // Telegram's limit is 4096
const TZ = CONFIG.tz || 'UTC';
const QUIET_FROM = CONFIG.quiet_from ?? 22;
const QUIET_TO = CONFIG.quiet_to ?? 8;
// Everything on a parked task while it sits in its parking state.
// source label → { state: parking state, who: a role name (per project) or people.yaml keys }
const PARKED = {
  'source:nightly': { state: 'Findings', who: 'questions' },
};
const PARKED_STATES = Object.values(PARKED).map((p) => p.state);
// Changes that also go to people who are not assignees: people.yaml keys + the line they get.
// `who` is a role name (resolved per project with ROLES) or a list of people.yaml keys.
// Reviewers review every task (task-protocol); the question owner answers triage's questions.
// From routing.yaml (approvers, question_owner; a project may override both).
const ROLES = CONFIG.roles || { default: { reviewers: [], questions: [] } };
const roleOf = (key, who) => (Array.isArray(who) ? who
  : (ROLES[String(key).split('-')[0]] || ROLES.default)[who] || ROLES.default[who] || []);
const WATCH = {
  state: { 'In Review': { who: 'reviewers', text: (o) => `👀 Ready for your review (State: ${o} → In Review)` } },
  labels: {
    question: { who: 'questions', text: () => '❓ Has a question for you (label question)' },
    'e2e:red': { who: 'questions', text: () => '🔴 E2E is red (label e2e:red)' },
  },
};

const now = Date.now();
const local = (ms) => {
  const parts = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
    .formatToParts(new Date(ms));
  const hour = Number(parts.find((x) => x.type === 'hour').value) % 24;
  const minute = parts.find((x) => x.type === 'minute').value;
  return { hour, hm: `${String(hour).padStart(2, '0')}:${minute}` };
};
const isQuiet = (ms) => {
  const { hour } = local(ms);
  return QUIET_FROM === QUIET_TO ? false : QUIET_FROM > QUIET_TO ? hour >= QUIET_FROM || hour < QUIET_TO : hour >= QUIET_FROM && hour < QUIET_TO;
};
if (isQuiet(now)) return [];

const token = $env.PLANE_NOTIFY_TOKEN;
if (!token) return [];
const mmEnabled = String($env.MM_DM_ENABLED || '').toLowerCase() === 'true';

const sd = $getWorkflowStaticData('global');
const dm = JSON.parse(JSON.stringify(sd.dm || {}));
for (const k of ['pending', 'tasks', 'deleted', 'sent']) dm[k] = dm[k] || {};
let changed = false;

// Code node helpers: `this.helpers` (also a global in n8n's task runner).
const http = (this && this.helpers) || (typeof helpers !== 'undefined' ? helpers : null);
const get = async (path) => {
  try {
    const r = await http.httpRequest({
      method: 'GET', url: API + path, headers: { 'X-API-Key': token }, json: true,
      returnFullResponse: true, ignoreHttpStatusErrors: true, timeout: 15000,
    });
    return { status: r.statusCode, body: r.body };
  } catch (e) {
    return { status: 0, body: null };
  }
};
const rows = (b) => (Array.isArray(b) ? b : (b && b.results) || []);
const ms = (t) => (t ? Date.parse(t) : 0);

// Candidates: marked tasks, deleted tasks and tasks updated in the last SCAN minutes.
const cand = {}; // issueId → projectId
for (const [id, m] of Object.entries(dm.pending)) if (PROJECTS[m.p]) cand[id] = m.p;
for (const [id, d] of Object.entries(dm.deleted)) if (PROJECTS[d.p]) cand[id] = d.p;
for (const pid of Object.keys(PROJECTS)) {
  const r = await get(`/projects/${pid}/issues/?order_by=-updated_at&per_page=20`);
  if (r.status !== 200) continue;
  for (const i of rows(r.body)) if (ms(i.updated_at) >= now - SCAN) cand[i.id] = cand[i.id] || pid;
}
if (!Object.keys(cand).length) return [];

// Workspace members: user id → { email, name }.
const members = {};
{
  const r = await get('/members/');
  for (const m of rows(r.body)) {
    const u = m.member || m;
    members[u.id] = { email: u.email || '', name: [u.first_name, u.last_name].filter(Boolean).join(' ') || u.display_name || '' };
  }
}
const personByEmail = (email) => Object.values(PEOPLE).find((p) => p.email && email && p.email.toLowerCase() === email.toLowerCase());
const userName = (id) => {
  const m = members[id] || {};
  const p = personByEmail(m.email);
  return (p && p.name ? p.name.split(' ')[0] : '') || m.name || 'someone';
};
const reachable = (p) => p && (p.chat_id || (mmEnabled && p.mattermost));

const strip = (html) => String(html || '').replace(/<(br|\/p|\/h\d|\/li|\/div)\b[^>]*>/gi, ' ').replace(/<[^>]*>/g, '')
  .replace(/&nbsp;/g, ' ').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
  .replace(/&amp;/g, '&').replace(/\s+/g, ' ').trim();
const cut = (t, n) => (t.length > n ? t.slice(0, n - 1).trimEnd() + '…' : t);
const val = (v) => (v === null || v === undefined || v === '' ? '—' : String(v));
const RELATIONS = { blocking: 'blocks', blocked_by: 'blocked by', relates_to: 'relates to', duplicate: 'duplicate of' };
const FIELDS = { name: 'Title', start_date: 'Start date', target_date: 'Due date', estimate_point: 'Estimate', parent: 'Parent', type: 'Type' };
const titleCase = (f) => FIELDS[f] || (f.charAt(0).toUpperCase() + f.slice(1)).replace(/_/g, ' ');

// One activity → { text, you?, extra? }. `you` = user id the line is about ("Assigned to you").
const describe = (a) => {
  const f = a.field;
  const o = a.old_value;
  const n = a.new_value;
  if (!f) return a.verb === 'created' ? { text: '🆕 Task created' } : null;
  if (f === 'description' || f === 'description_html') return { text: `📝 Description updated: “${cut(strip(n), 140)}”`, kind: 'description' };
  if (f === 'state') {
    const w = WATCH.state[n];
    return { text: `State: ${val(o)} → ${val(n)}`, fromBacklog: PARKED_STATES.includes(o), ...(w ? { watch: w.who, watchText: w.text(val(o)) } : {}) };
  }
  if (f === 'priority') return { text: `Priority: ${val(o)} → ${val(n)}` };
  if (f === 'labels') {
    const w = n ? WATCH.labels[n] : null;
    return { text: n ? `Label added: ${n}` : `Label removed: ${val(o)}`, ...(w ? { watch: w.who, watchText: w.text() } : {}) };
  }
  if (f === 'assignees') {
    if (n || a.new_identifier) {
      const who = a.new_identifier ? userName(a.new_identifier) : n;
      return { text: `Assignee added: ${who}`, you: a.new_identifier, youText: 'Assigned to you' };
    }
    const who = a.old_identifier ? userName(a.old_identifier) : val(o);
    return { text: `Assignee removed: ${who}`, you: a.old_identifier, youText: 'Unassigned you', extra: a.old_identifier };
  }
  if (RELATIONS[f]) return { text: n ? `Relation added: ${RELATIONS[f]} ${n}` : `Relation removed: ${RELATIONS[f]} ${val(o)}` };
  if (f === 'cycles' || f === 'modules') {
    const what = f === 'cycles' ? 'cycle' : 'module';
    return { text: n ? `Added to ${what} ${n}` : `Removed from ${what} ${val(o)}` };
  }
  if (f === 'archived_at') return { text: n ? 'Archived' : 'Restored from archive' };
  if (f === 'link' || f === 'attachment') {
    const c = String(a.comment || '').trim();
    return { text: `${c ? c.charAt(0).toUpperCase() + c.slice(1) : titleCase(f)}${n || o ? ': ' + (n || o) : ''}` };
  }
  return { text: `${titleCase(f)}: ${val(o)} → ${val(n)}` };
};

// Per project, label and state names (for backlog tasks), read once per run.
const meta = {};
const projectMeta = async (pid) => {
  if (!meta[pid]) {
    const [l, s] = [await get(`/projects/${pid}/labels/`), await get(`/projects/${pid}/states/`)];
    meta[pid] = {
      ok: l.status === 200 && s.status === 200,
      labels: Object.fromEntries(rows(l.body).map((x) => [x.id, x.name])),
      states: Object.fromEntries(rows(s.body).map((x) => [x.id, x.name])),
    };
  }
  return meta[pid];
};

// Collect events per task: { at, actor, text, you, youText, extra }.
const tasks = {}; // issueId → { key, title, link, assignees: [userId], events: [], maxAt, base }
for (const [id, pid] of Object.entries(cand)) {
  const s = dm.sent[id] || {};
  const marker = dm.pending[id];
  const base = s.base || (marker ? marker.t - LEAD : now - SCAN);
  const del = dm.deleted[id];
  const issue = await get(`/projects/${pid}/issues/${id}/?expand=assignees`);
  let t;
  if (issue.status === 200 && issue.body && !issue.body.deleted_at) {
    const i = issue.body;
    const key = `${PROJECTS[pid]}-${i.sequence_id}`;
    t = { key, title: i.name || '', assignees: (i.assignees || []).map((a) => (typeof a === 'string' ? a : a.id)), events: [] };
    const pm = await projectMeta(pid);
    if (!pm.ok) continue; // try again next minute
    const parkedIn = (i.labels || []).map((l) => PARKED[pm.labels[typeof l === 'string' ? l : l.id]]).find(Boolean);
    t.backlog = Boolean(parkedIn);
    t.inBacklog = Boolean(parkedIn) && pm.states[i.state] === parkedIn.state;
    t.watchers = parkedIn ? roleOf(key, parkedIn.who) : [];
    const acts = await get(`/projects/${pid}/issues/${id}/activities/?order_by=-created_at&per_page=50`);
    const comments = await get(`/projects/${pid}/issues/${id}/comments/?order_by=-created_at&per_page=50`);
    if (acts.status !== 200 || comments.status !== 200) continue; // try again next minute
    for (const a of rows(acts.body).reverse()) { // oldest first (ties keep Plane's order)
      const at = ms(a.created_at);
      if (at <= base || at > now || a.field === 'comment') continue;
      const d = describe(a);
      if (d) t.events.push({ at, actor: a.actor, ...d });
    }
    for (const c of rows(comments.body).reverse()) {
      const created = ms(c.created_at);
      const edited = ms(c.edited_at);
      const who = userName(c.actor);
      const excerpt = cut(strip(c.comment_html || c.comment_stripped || ''), 200);
      if (created > base && created <= now) t.events.push({ at: created, actor: c.actor, text: `💬 ${who}: ${excerpt}` });
      else if (edited > base && edited <= now) t.events.push({ at: edited, actor: c.actor, text: `💬 ${who} edited a comment: ${excerpt}` });
    }
    // Several description saves in one batch: keep only the newest.
    const descs = t.events.filter((e) => e.kind === 'description').sort((x, y) => y.at - x.at);
    t.events = t.events.filter((e) => e.kind !== 'description' || e === descs[0]);
  } else if (issue.status === 404 || (issue.status === 200 && issue.body && issue.body.deleted_at)) {
    const known = dm.tasks[id];
    if (!del || !known) { // nothing to tell anyone; forget it
      if (dm.pending[id] || dm.deleted[id]) { delete dm.pending[id]; delete dm.deleted[id]; changed = true; }
      continue;
    }
    t = { key: known.key, title: known.title, assignees: known.assignees || [], events: [] };
    if (del.at > base) t.events.push({ at: del.at, actor: del.actor, text: `🗑 Task deleted${del.actor ? ' by ' + userName(del.actor) : ''}` });
  } else {
    continue; // Plane unreachable or no access: leave it for the next run
  }
  for (const e of t.events) {
    const by = e.actor && !/^(💬|🗑)/.test(e.text) ? `by ${userName(e.actor)} · ` : '';
    e.suffix = ` (${by}${local(e.at).hm})`;
  }
  t.events.sort((x, y) => x.at - y.at);
  t.link = `${PLANE_URL}/${WORKSPACE}/browse/${t.key}/`;
  t.maxAt = t.events.reduce((m, e) => Math.max(m, e.at), base);
  t.base = base;
  t.pid = pid;
  tasks[id] = t;
}

// Who should hear about each event, and which events each person has not been sent yet.
const uidByEmail = (email) => Object.keys(members).find((id) => email && (members[id].email || '').toLowerCase() === email.toLowerCase());
const recipientsOf = (t, e) => {
  const keys = [...roleOf(t.key, e.watch || []), ...(t.backlog && (t.inBacklog || e.fromBacklog) ? t.watchers : [])];
  const watchers = new Set(keys.map((k) => PEOPLE[k] && uidByEmail(PEOPLE[k].email)).filter(Boolean));
  const ids = new Set([...t.assignees, ...(e.extra ? [e.extra] : []), ...watchers]);
  ids.delete(e.actor);
  const out = [];
  for (const uid of ids) {
    const p = personByEmail((members[uid] || {}).email);
    if (reachable(p)) out.push({ uid, p, watcher: watchers.has(uid) });
  }
  return out;
};
const cursor = (id, email) => {
  const s = dm.sent[id] || {};
  return Math.max(tasks[id].base, (s.r || {})[email] || 0);
};
const inbox = {}; // email → { p, oldest, items: { issueId: [line] } }
for (const [id, t] of Object.entries(tasks)) {
  for (const e of t.events) {
    for (const { uid, p, watcher } of recipientsOf(t, e)) {
      if (e.at <= cursor(id, p.email)) continue;
      const box = (inbox[p.email] = inbox[p.email] || { p, oldest: Infinity, quiet: false, items: {} });
      box.oldest = Math.min(box.oldest, e.at);
      box.quiet = box.quiet || isQuiet(e.at);
      const line = e.you && e.you === uid ? e.youText : watcher && e.watchText ? e.watchText : e.text;
      (box.items[id] = box.items[id] || []).push(line + e.suffix);
    }
  }
}

// Messages for everyone whose oldest unsent change is at least WINDOW old.
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
// blocks: [[taskHeader, line, line…], …] → texts of at most MAX_LEN chars. A task that does not
// fit continues in the next message under its header again.
const split = (header, blocks, more) => {
  const chunks = [];
  let cur = header;
  for (const [head, ...lines] of blocks) {
    const room = MAX_LEN - header.length - more.length - head.length - 4;
    let started = false;
    for (const line of lines.map((l) => cut(l, room))) {
      const add = started ? '\n' + line : '\n\n' + head + '\n' + line;
      if (cur.length + add.length > MAX_LEN) {
        chunks.push(cur);
        cur = header + more + '\n\n' + head + '\n' + line;
      } else cur += add;
      started = true;
    }
  }
  chunks.push(cur);
  return chunks;
};
const out = [];
const due = Object.values(inbox).filter((b) => b.oldest <= now - WINDOW);
for (const box of due) {
  const ids = Object.keys(box.items).sort((a, b) => tasks[a].events[0].at - tasks[b].events[0].at);
  const n = ids.length;
  const title = `${box.quiet ? 'Overnight digest' : 'Plane updates'}: ${n} task${n === 1 ? '' : 's'}`;
  if (box.p.chat_id) {
    const blocks = ids.map((id) => [`<b><a href="${esc(tasks[id].link)}">${esc(tasks[id].key)}</a></b> ${esc(tasks[id].title)}`,
      ...box.items[id].map((l) => '• ' + esc(l))]);
    for (const text of split(`<b>${esc(title)}</b>`, blocks, ' <i>(continued)</i>')) out.push({ json: { kind: 'telegram', chat_id: String(box.p.chat_id), text } });
  }
  if (mmEnabled && box.p.mattermost) {
    const blocks = ids.map((id) => [`**[${tasks[id].key}](${tasks[id].link})** ${tasks[id].title}`, ...box.items[id].map((l) => '- ' + l)]);
    for (const text of split(`**${title}**`, blocks, ' _(continued)_')) out.push({ json: { kind: 'mattermost', channel: '@' + box.p.mattermost, text } });
  }
  for (const id of ids) {
    const s = (dm.sent[id] = dm.sent[id] || { base: tasks[id].base, r: {} });
    s.base = s.base || tasks[id].base;
    s.r = s.r || {};
    s.r[box.p.email] = tasks[id].maxAt;
    s.u = now;
  }
  changed = true;
}

// Drop markers of tasks nobody is still waiting on, and old state.
for (const [id, t] of Object.entries(tasks)) {
  const waiting = t.events.some((e) => recipientsOf(t, e).some(({ p }) => e.at > cursor(id, p.email)));
  if (!waiting && (dm.pending[id] || dm.deleted[id])) {
    delete dm.pending[id];
    delete dm.deleted[id];
    changed = true;
  }
}
// Every event older than SCAN has been sent by now (anything that old is due), so a person assigned
// later does not get the task's old history.
if (changed) for (const id of Object.keys(tasks)) if (dm.sent[id]) dm.sent[id].base = Math.max(dm.sent[id].base || 0, now - SCAN);
for (const [k, m] of [['pending', 't'], ['deleted', 'at'], ['sent', 'u'], ['tasks', 'u']]) {
  for (const [id, v] of Object.entries(dm[k])) {
    if (!(v[m] > now - (k === 'tasks' ? 10 * KEEP : KEEP))) { delete dm[k][id]; changed = true; }
  }
}

if (changed) sd.dm = dm; // only write when needed: every write can race with "Collect DMs"
return out;
