// Offline tests for the plane-notify Code nodes: dm-collect.js + dm-flush.js (private messages)
// and plane-notify.js (Mattermost channel pings). Fake $input, static data, $env, Plane API
// and clock. Run: bash tests/n8n-dm.test.sh
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const FLOWS = join(dirname(fileURLToPath(import.meta.url)), '..', 'infra', 'n8n', 'flows');
const P1 = 'proj-bil';
const P2 = 'proj-prc';
const P3 = 'proj-anl';
const PROJECTS = { [P1]: 'BIL', [P2]: 'PRC', [P3]: 'ANL' };
const PEOPLE = {
  alex: { name: 'Alex Morgan', email: 'alex@x', telegram: 'alex_m', mattermost: 'alex', chat_id: '101' },
  sam: { name: 'Sam', email: 'sam@x', telegram: 'samr', mattermost: '', chat_id: '102' },
  noor: { name: 'Noor', email: 'noor@x', telegram: '', mattermost: 'noor', chat_id: '104' },
  kenji: { name: 'Kenji', email: 'kenji@x', telegram: 'kenji_s', mattermost: '', chat_id: '103' },
};
const USERS = {
  uAlex: { email: 'alex@x', first_name: 'Alex', last_name: 'Morgan', display_name: 'alex' },
  uSam: { email: 'sam@x', first_name: 'Sam Rivera', last_name: '', display_name: 'sam' },
  uNoor: { email: 'noor@x', first_name: 'Noor', last_name: '', display_name: 'noor' },
  uKenji: { email: 'kenji@x', first_name: 'Kenji', last_name: '', display_name: 'kenji' },
  uBot: { email: 'bot@x', first_name: 'Triage Bot', last_name: '', display_name: 'triage-bot' },
};

let fail = 0;
const ok = (cond, label, extra) => {
  if (cond) console.log(`ok   ${label}`);
  else { console.log(`FAIL ${label}${extra !== undefined ? '\n     ' + JSON.stringify(extra).slice(0, 1500) : ''}`); fail = 1; }
};

// ---- clock -------------------------------------------------------------------------------
let NOW = 0;
const local = (iso) => Date.parse(iso + '+05:30'); // CONFIG.tz below; e.g. local('2026-10-20T10:00:00')
class FakeDate extends Date {
  constructor(...a) { super(...(a.length ? a : [NOW])); }
  static now() { return NOW; }
}
const iso = (ms) => new Date(ms).toISOString();

// ---- fake Plane ---------------------------------------------------------------------------
let plane;
const resetPlane = () => { plane = { issues: {}, calls: 0, down: false }; };
const issue = (id, p, seq, name, assignees) => {
  plane.issues[id] = { id, project: p, sequence_id: seq, name, assignees, updated_at: iso(NOW), activities: [], comments: [], deleted: false };
  return plane.issues[id];
};
const act = (id, actor, field, old_value, new_value, extra = {}) => {
  const i = plane.issues[id];
  i.activities.push({ id: `a${i.activities.length}`, verb: field ? 'updated' : 'created', field, old_value, new_value, actor, created_at: iso(NOW), old_identifier: null, new_identifier: null, ...extra });
  i.updated_at = iso(NOW);
};
const comment = (id, actor, html) => {
  plane.issues[id].comments.push({ id: `c${plane.issues[id].comments.length}`, actor, comment_html: html, created_at: iso(NOW), edited_at: null });
};
const helpers = {
  async httpRequest(o) {
    plane.calls++;
    if (o.headers['X-API-Key'] !== 'tok') return { statusCode: 401, body: {} };
    if (plane.down) throw new Error('ECONNREFUSED');
    const u = new URL(o.url);
    const path = u.pathname.replace('/api/v1/workspaces/acme', '');
    let m;
    if (/^\/projects\/[^/]+\/labels\/$/.test(path)) return { statusCode: 200, body: { results: [{ id: 'lb-x', name: 'team:data' }, { id: 'lb-ng', name: 'source:nightly' }] } };
    if (/^\/projects\/[^/]+\/states\/$/.test(path)) return { statusCode: 200, body: { results: [{ id: 'st-bl', name: 'Backlog' }, { id: 'st-in', name: 'Inbox' }, { id: 'st-td', name: 'Todo' }, { id: 'st-fd', name: 'Findings' }] } };
    if (path === '/members/') return { statusCode: 200, body: Object.entries(USERS).map(([id, x]) => ({ id, ...x })) };
    if ((m = path.match(/^\/projects\/([^/]+)\/issues\/$/))) {
      const list = Object.values(plane.issues).filter((i) => i.project === m[1] && !i.deleted)
        .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
      return { statusCode: 200, body: { results: list.map(({ activities, comments, ...i }) => i) } };
    }
    if ((m = path.match(/^\/projects\/([^/]+)\/issues\/([^/]+)\/(activities|comments)?\/?$/))) {
      const i = plane.issues[m[2]];
      if (!i || i.deleted || i.project !== m[1]) return { statusCode: 404, body: { error: 'not found' } };
      if (!m[3]) {
        const { activities, comments, ...rest } = i;
        return { statusCode: 200, body: { ...rest, assignees: i.assignees.map((id) => ({ id, ...USERS[id] })) } };
      }
      return { statusCode: 200, body: { results: [...i[m[3]]].reverse() } };
    }
    return { statusCode: 404, body: {} };
  },
};

// ---- node runners -------------------------------------------------------------------------
const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
const load = (file, subs) => {
  let code = readFileSync(join(FLOWS, file), 'utf8');
  for (const [k, v] of Object.entries(subs)) code = code.replace(k, v);
  return new AsyncFunction('$input', '$getWorkflowStaticData', '$env', 'Date', code);
};
const CONFIG = {
  web_url: 'https://plane.example.com', workspace: 'acme', tz: 'Asia/Kolkata', quiet_from: 22, quiet_to: 8,
  roles: { default: { reviewers: ['sam', 'alex'], questions: ['alex'] }, ANL: { reviewers: ['kenji', 'sam', 'alex'], questions: ['kenji'] } },
};
const subs = { __PROJECTS__: JSON.stringify(PROJECTS), __PEOPLE__: JSON.stringify(PEOPLE), __CONFIG__: JSON.stringify(CONFIG), __PLANE_API__: JSON.stringify('http://plane.test') };
const collectFn = load('dm-collect.js', subs);
const flushFn = load('dm-flush.js', subs);
let static_;
let env;
const reset = () => { resetPlane(); static_ = {}; env = { PLANE_NOTIFY_TOKEN: 'tok' }; };
const input = (body) => ({ first: () => ({ json: { body } }), all: () => [{ json: { body } }] });
const collect = (body) => collectFn.call({ helpers }, input(body), () => static_, env, FakeDate);
const flush = async () => (await flushFn.call({ helpers }, input({}), () => static_, env, FakeDate)).map((x) => x.json);
// Webhook payload like Plane CE sends (only what the nodes read).
const hook = (id, action = 'updated', actorId = 'uBot') => {
  const i = plane.issues[id];
  return {
    event: 'issue', action,
    data: { id, project: i.project, sequence_id: i.sequence_id, name: i.name, assignees: i.assignees.map((a) => ({ id: a, ...USERS[a] })) },
    activity: { field: 'state', actor: { id: actorId, ...USERS[actorId] } },
  };
};
const commentHook = (id, actorId) => ({ event: 'issue_comment', action: 'created', data: { id: 'cmt', issue: id, project: plane.issues[id].project, comment_html: '<p>x</p>' }, activity: { field: 'comment', actor: { id: actorId, ...USERS[actorId] } } });
const tg = (msgs, chat) => msgs.filter((m) => m.kind === 'telegram' && m.chat_id === chat);
const min = 60 * 1000;

// ---- dm-collect ---------------------------------------------------------------------------
reset();
NOW = local('2026-10-20T10:00:00');
issue('i1', P1, 1, 'Language switch', ['uNoor']);
let r = await collect(hook('i1'));
ok(Array.isArray(r) && r.length === 0, 'collect returns nothing');
ok(static_.dm.pending.i1 && static_.dm.pending.i1.t === NOW, 'collect marks the task');
NOW += 1000;
await collect(hook('i1'));
ok(static_.dm.pending.i1.t === NOW - 1000, 'collect keeps the first-seen time of a marked task');
ok(static_.dm.tasks.i1.key === 'BIL-1' && static_.dm.tasks.i1.assignees[0] === 'uNoor', 'collect remembers key and assignees');
await collect({ event: 'issue', action: 'updated', data: { id: 'zz', project: 'other' }, activity: {} });
await collect({ event: 'ping' });
ok(!static_.dm.pending.zz, 'collect ignores other projects and non-task events');
await collect(commentHook('i1', 'uSam'));
ok(Object.keys(static_.dm.pending).length === 1, 'collect: comment event marks its task');

// ---- own change skipped -------------------------------------------------------------------
reset();
NOW = local('2026-10-20T10:00:00');
issue('i1', P1, 1, 'Language switch', ['uNoor']);
act('i1', 'uNoor', 'state', 'Todo', 'In Progress');
await collect(hook('i1', 'updated', 'uNoor'));
NOW += 6 * min;
r = await flush();
ok(r.length === 0, 'own change: no DM to the person who made it', r);
ok(!static_.dm.pending.i1, 'own change: marker dropped (nobody waiting)');

// ---- all the fields, batching, two tasks ----------------------------------------------------
reset();
NOW = local('2026-10-20T10:00:00');
issue('i1', P1, 1, 'Language switch', ['uNoor']);
issue('i2', P1, 2, 'Dates in the user locale', ['uNoor', 'uAlex']);
NOW += 1000;
act('i1', 'uBot', 'state', 'Todo', 'In Progress');
act('i1', 'uBot', 'priority', 'none', 'high');
act('i1', 'uBot', 'labels', '', 'team:frontend');
act('i1', 'uBot', 'labels', 'team:design', '');
act('i1', 'uAlex', 'assignees', '', 'noor', { new_identifier: 'uNoor' });
act('i1', 'uAlex', 'description', '<p>old</p>', '<h2>Goal</h2><p>Switch &amp; keep RTL</p>');
act('i1', 'uAlex', 'target_date', null, '2026-10-25');
act('i1', 'uAlex', 'blocking', '', 'BIL-5');
act('i1', 'uAlex', 'name', 'Lang', 'Language switch');
comment('i1', 'uSam', '<p>Please check the <b>header</b></p>');
for (let k = 0; k < 6; k++) await collect(hook('i1')); // one webhook per field, same task
NOW += 2 * min;
act('i2', 'uSam', 'state', 'Todo', 'In Review');
await collect(hook('i2', 'updated', 'uSam'));
NOW += 1 * min;
r = await flush();
ok(r.length === 0, 'flush before 5 minutes: nothing', r);
NOW += 3 * min; // 6 min after the first change
r = await flush();
let noor = tg(r, '104');
ok(noor.length === 1, 'several fields on two tasks → one message for Noor', r);
const t = noor[0] ? noor[0].text : '';
for (const want of [
  'State: Todo → In Progress (by Triage Bot · 10:00)',
  'Priority: none → high',
  'Label added: team:frontend',
  'Label removed: team:design',
  'Assigned to you (by Alex · 10:00)',
  '📝 Description updated: “Goal Switch &amp; keep RTL”',
  'Due date: — → 2026-10-25',
  'Relation added: blocks BIL-5',
  'Title: Lang → Language switch',
  '💬 Sam: Please check the header (10:00)',
  '<a href="https://plane.example.com/acme/browse/BIL-1/">BIL-1</a>',
  'BIL-2',
  'State: Todo → In Review (by Sam · 10:02)',
  'Plane updates: 2 tasks',
]) ok(t.includes(want), `line: ${want}`, t);
ok(t.indexOf('State: Todo → In Progress') < t.indexOf('Title: Lang') && t.indexOf('BIL-1') < t.indexOf('BIL-2'), 'lines in order of change, tasks in order of first change');
const alex = tg(r, '101');
ok(alex.length === 0, 'Alex not due yet (his oldest change, on BIL-2, is 3 min old)', alex);
ok(!r.some((m) => m.kind === 'mattermost'), 'MM disabled → no Mattermost items');
r = await flush();
ok(tg(r, '104').length === 0, 'already sent → not sent again');
NOW += 2 * min;
r = await flush();
ok(tg(r, '101').length === 1 && tg(r, '101')[0].text.includes('In Review') && !tg(r, '101')[0].text.includes('BIL-1'),
  'Alex gets only BIL-2 once his window is over', r);
ok(tg(r, '104').length === 0, 'Noor gets nothing new');
ok(!static_.dm.pending.i1 && !static_.dm.pending.i2, 'markers dropped after everyone got their message');

// ---- reviewers / Alex hear about tasks that aren't theirs (no group post any more) ----------
reset();
NOW = local('2026-10-20T10:30:00');
issue('i5', P1, 5, 'Kenji task', ['uKenji']);
issue('i6', P1, 6, 'Noor task', ['uNoor']);
NOW += 1000;
act('i5', 'uKenji', 'state', 'In Progress', 'In Review');
act('i6', 'uBot', 'labels', '', 'question');
act('i6', 'uBot', 'labels', '', 'e2e:red');
act('i6', 'uBot', 'labels', '', 'team:frontend');
await collect(hook('i5', 'updated', 'uKenji'));
await collect(hook('i6', 'updated', 'uBot'));
NOW += 6 * min;
r = await flush();
const alexR = tg(r, '101')[0] ? tg(r, '101')[0].text : '';
const samR = tg(r, '102')[0] ? tg(r, '102')[0].text : '';
ok(alexR.includes('👀 Ready for your review (State: In Progress → In Review)') && alexR.includes('BIL-5'), 'In Review → Alex (not assignee)', alexR);
ok(alexR.includes('❓ Has a question for you') && alexR.includes('🔴 E2E is red') && !alexR.includes('team:frontend'), 'question / e2e:red → Alex, other labels not', alexR);
ok(samR.includes('👀 Ready for your review') && !samR.includes('BIL-6'), 'In Review → Sam; Alex-only labels not to Sam', samR);
ok(tg(r, '104')[0] && tg(r, '104')[0].text.includes('Label added: question'), 'assignee keeps the plain lines', r);
// ANL: Kenji reviews too and owns the questions (routing.yaml projects.ANL)
NOW += 1000;
issue('i7', P3, 7, 'ANL task', ['uNoor']);
NOW += 1000;
act('i7', 'uNoor', 'state', 'In Progress', 'In Review');
act('i7', 'uBot', 'labels', '', 'question');
await collect(hook('i7', 'updated', 'uBot'));
NOW += 6 * min;
r = await flush();
const anl = (chat) => tg(r, chat).map((m) => m.text).join('\n');
ok(anl('103').includes('ANL-7') && anl('103').includes('👀 Ready for your review') && anl('103').includes('❓ Has a question for you'),
  'ANL: In Review + question → Kenji', r);
ok(anl('101').includes('ANL-7') && anl('101').includes('👀 Ready for your review') && !anl('101').includes('❓'),
  'ANL: Alex reviews but the question goes to Kenji', anl('101'));
NOW += 1000;
act('i5', 'uAlex', 'state', 'In Review', 'Done');
act('i6', 'uAlex', 'state', 'Todo', 'In Review');
await collect(hook('i5', 'updated', 'uAlex'));
await collect(hook('i6', 'updated', 'uAlex'));
NOW += 6 * min;
r = await flush();
ok(tg(r, '101').length === 0, 'reviewer who made the change hears nothing', tg(r, '101'));
ok(tg(r, '102').length === 1 && !tg(r, '102')[0].text.includes('BIL-5'), 'Done does not go to reviewers', r);

// ---- nightly findings: Alex only, while in Findings ------------------------------------------
reset();
NOW = local('2026-10-20T13:00:00');
issue('f1', P1, 70, 'hono 4.12.30 has known vulnerabilities', []);
Object.assign(plane.issues.f1, { labels: ['lb-ng'], state: 'st-fd' });
NOW += 1000;
act('f1', 'uBot', null, null, null);
await collect(hook('f1', 'created', 'uBot'));
NOW += 6 * min;
r = await flush();
ok(tg(r, '101').length === 1 && tg(r, '101')[0].text.includes('BIL-70'), 'finding → Alex', r);
ok(tg(r, '103').length === 0, 'finding → not Kenji', r);

// ---- unassigned person, scan without marker, MM on -------------------------------------------
reset();
env.MM_DM_ENABLED = 'true';
NOW = local('2026-10-20T11:00:00');
issue('i3', P2, 7, 'Pricing job', ['uAlex']);
act('i3', 'uSam', 'assignees', 'noor', '', { old_identifier: 'uNoor' }); // no webhook: found by the scan
NOW += 6 * min;
r = await flush();
noor = tg(r, '104');
ok(noor.length === 1 && noor[0].text.includes('Unassigned you (by Sam') && noor[0].text.includes('PRC-7'), 'removed assignee hears about it (scan, no marker)', r);
ok(tg(r, '101').length === 1 && tg(r, '101')[0].text.includes('Assignee removed: Noor'), 'remaining assignee sees "Assignee removed"', r);
const mm = r.filter((m) => m.kind === 'mattermost');
ok(mm.length === 2 && mm.some((m) => m.channel === '@noor') && mm.some((m) => m.channel === '@alex') && mm[0].text.includes('**[PRC-7]('),
  'MM enabled → Mattermost DMs to @user in markdown', mm);

// ---- quiet hours ----------------------------------------------------------------------------
reset();
NOW = local('2026-10-20T23:30:00');
issue('i1', P1, 1, 'Language switch', ['uNoor']);
act('i1', 'uAlex', 'state', 'Todo', 'Blocked');
await collect(hook('i1', 'updated', 'uAlex'));
NOW += 6 * min;
const callsBefore = plane.calls;
r = await flush();
ok(r.length === 0 && plane.calls === callsBefore, '23:36 local time: held (no call, no message)');
NOW = local('2026-10-21T07:59:00');
ok((await flush()).length === 0, '07:59: still held');
NOW = local('2026-10-21T08:00:00');
r = await flush();
noor = tg(r, '104');
ok(noor.length === 1 && noor[0].text.startsWith('<b>Overnight digest: 1 task</b>') && noor[0].text.includes('Blocked (by Alex · 23:30)'),
  '08:00: overnight digest', r);

// ---- long buffer, escaping ------------------------------------------------------------------
reset();
NOW = local('2026-10-20T12:00:00');
issue('i1', P1, 1, 'Fix <script> & "quotes" > here', ['uNoor']);
for (let k = 0; k < 60; k++) comment('i1', 'uSam', `<p>comment ${k} ${'x'.repeat(180)} a < b</p>`);
await collect(commentHook('i1', 'uSam'));
NOW += 6 * min;
r = await flush();
noor = tg(r, '104');
ok(noor.length >= 3 && noor.every((m) => m.text.length <= 3900), `long buffer split into ${noor.length} messages ≤ 3900 chars`, noor.map((m) => m.text.length));
ok(noor.every((m) => m.text.includes('BIL-1')) && noor.slice(1).every((m) => m.text.includes('(continued)')), 'every part repeats the task header');
const all = noor.map((m) => m.text).join('\n');
ok(all.includes('Fix &lt;script&gt; &amp; "quotes" &gt; here') && !all.includes('<script>') && all.includes('a &lt; b'), 'HTML escaping of < & >');
ok((all.match(/💬 Sam: comment/g) || []).length === 60, 'no comment lost across parts');

// ---- deleted task, Plane down, no token -------------------------------------------------------
reset();
NOW = local('2026-10-20T14:00:00');
issue('i9', P1, 9, 'Old idea', ['uNoor']);
await collect(hook('i9', 'updated', 'uAlex')); // remembers title + assignees
plane.issues.i9.deleted = true;
await collect({ event: 'issue', action: 'deleted', data: { id: 'i9' }, activity: { actor: { id: 'uAlex', ...USERS.uAlex } } });
NOW += 6 * min;
r = await flush();
noor = tg(r, '104');
ok(noor.length === 1 && noor[0].text.includes('🗑 Task deleted by Alex') && noor[0].text.includes('Old idea'), 'deleted task reported from memory', r);
reset();
NOW = local('2026-10-20T14:00:00');
issue('i1', P1, 1, 'Language switch', ['uNoor']);
act('i1', 'uAlex', 'state', 'Todo', 'Done');
await collect(hook('i1', 'updated', 'uAlex'));
NOW += 6 * min;
plane.down = true;
r = await flush();
ok(r.length === 0 && static_.dm.pending.i1, 'Plane unreachable: nothing sent, marker kept');
plane.down = false;
env.PLANE_NOTIFY_TOKEN = '';
ok((await flush()).length === 0, 'no PLANE_NOTIFY_TOKEN: nothing');
env.PLANE_NOTIFY_TOKEN = 'tok';
ok(tg(await flush(), '104').length === 1, 'recovers on the next run');

// ---- "Route event" (plane-notify.js, Mattermost channel only) unchanged --------------------------------------
{
  const STATES = { s1: 'Todo', s2: 'In Review', s3: 'Done' };
  const PEOPLE_RT = { ...PEOPLE, alex: { ...PEOPLE.alex, mattermost: '' } };
  const route = load('plane-notify.js', {
    __PROJECTS__: JSON.stringify(PROJECTS), __TOPICS__: JSON.stringify({ BIL: 8237, PRC: 8236 }),
    __STATES__: JSON.stringify(STATES), __PEOPLE__: JSON.stringify(PEOPLE_RT), __CONFIG__: JSON.stringify(CONFIG),
  });
  const run = async (body) => (await route(input(body), () => ({}), {}, Date)).map((x) => x.json);
  const data = { id: 'i1', project: P1, sequence_id: 3, name: 'A <b> title', assignees: [{ id: 'uNoor', email: 'noor@x', display_name: 'noor' }], labels: [{ id: 'l1', name: 'question' }] };
  let m = await run({ event: 'issue', action: 'updated', data, activity: { field: 'state', new_value: 's2', actor: { display_name: 'noor' } } });
  ok(m.length === 1 && m[0].topic === 8237 && m[0].tg === '@samr @alex_m ready for review:\nBIL-3 A &lt;b&gt; title\nhttps://plane.example.com/acme/browse/BIL-3/', 'route: In Review → Sam + Alex in the BIL topic', m);
  m = await run({ event: 'issue', action: 'updated', data, activity: { field: 'assignees', old_value: [], new_value: ['uNoor'], actor: { display_name: 'alex' } } });
  ok(m.length === 1 && m[0].tg.startsWith('Noor assigned to you by alex:'), 'route: assignment ping', m);
  m = await run({ event: 'issue', action: 'updated', data, activity: { field: 'labels', old_value: [], new_value: ['l1'], actor: {} } });
  ok(m.length === 1 && m[0].tg.startsWith('@alex_m has a question for you:'), 'route: question label → the question owner', m);
  m = await run({ event: 'issue', action: 'updated', data: { ...data, project: P2 }, activity: { field: 'state', new_value: 's3', actor: { display_name: 'bot' } } });
  ok(m.length === 1 && m[0].topic === 8236 && m[0].tg.startsWith('→ Done (by bot):'), 'route: Done → channel, PRC topic', m);
  m = await run({ event: 'issue', action: 'updated', data: { ...data, project: P3 }, activity: { field: 'state', new_value: 's2', actor: {} } });
  ok(m.length === 1 && m[0].tg.startsWith('@kenji_s @samr @alex_m ready for review:') && m[0].topic === undefined, 'route: ANL review → Kenji, Sam, Alex; no topic', m);
  m = await run({ event: 'issue_comment', action: 'created', data: { issue: 'i1', project: P1 }, activity: {} });
  ok(m.length === 0, 'route: comments do not go to the group');
}

process.exit(fail);
