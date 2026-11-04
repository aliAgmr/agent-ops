// Code node of the plane-notify flow (source of truth; build-flow.sh embeds it in plane-notify.json).
// Input: one Plane webhook delivery. Output: zero or more items { tg, mm }. Only `mm` is used: it feeds
// the Mattermost channel. Telegram no longer posts to the group; the same pings go privately through
// dm-flush.js (WATCH there).
//
// Rules (infra/n8n/README.md); reviewers and question owner per project come from routing.yaml:
//   work item assigned            → ping the new assignees
//   label question added          → ping the question owner
//   label e2e:red added           → ping the assignees + the question owner
//   state → In Review             → ping the reviewers (approvers review every task)
//   state → Ready for E2E / Done  → post to the channel

// Team settings (filled by build-flow.sh): { web_url, workspace, roles: { default: …, <KEY>: … } }
const CONFIG = __CONFIG__;
const PLANE_URL = CONFIG.web_url;
const WORKSPACE = CONFIG.workspace;
// Plane project id → identifier, and state id → name across those projects (filled by build-flow.sh).
const PROJECTS = __PROJECTS__;
// Project identifier → Telegram topic (message_thread_id) its messages go to.
const TOPICS = __TOPICS__;
const STATES = __STATES__;
// Plane email → people.yaml key. Handles come from people.yaml; empty handle = plain name, no @mention.
const PEOPLE = __PEOPLE__;

const body = $input.first().json.body || {};
const { event, action, data = {}, activity = {} } = body;
if (event !== 'issue' || !PROJECTS[data.project]) return [];

const key = `${PROJECTS[data.project]}-${data.sequence_id}`;
const link = `${PLANE_URL}/${WORKSPACE}/browse/${key}/`;
const title = `${key} ${data.name || ''}`.trim();
const actor = activity.actor ? (activity.actor.display_name || activity.actor.email || '') : '';

const personByEmail = (email) => Object.values(PEOPLE).find((p) => p.email && p.email === email);
const mention = (p, where) => (p && p[where] ? '@' + p[where] : p ? p.name : '');
const people = (list) => list.filter(Boolean);
const assignees = () => people((data.assignees || []).map((a) => personByEmail(a.email) || { name: a.display_name || a.email }));
const names = (list, where) => [...new Set(list.map((p) => mention(p, where)).filter(Boolean))].join(' ');

const msgs = [];
// Telegram gets parse_mode HTML (the node's default Markdown mangles titles with _ * [ ]), so escape.
const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const say = (who, what) => msgs.push({
  topic: TOPICS[PROJECTS[data.project]],
  tg: esc(`${names(who, 'telegram')} ${what}\n${title}\n${link}`.trim()),
  mm: `${names(who, 'mattermost')} ${what}\n[${title}](${link})`.trim(),
});

const labelName = (id) => ((data.labels || []).find((l) => l.id === id) || {}).name;
const added = (oldV, newV) => (newV || []).filter((x) => !(oldV || []).includes(x));
// Reviewers and question owner per project (routing.yaml approvers / question_owner).
const ROLES = CONFIG.roles || {};
const role = ROLES[PROJECTS[data.project]] || ROLES.default || { reviewers: [], questions: [] };
const reviewers = () => people(role.reviewers.map((k) => PEOPLE[k]));
const owners = () => people(role.questions.map((k) => PEOPLE[k]));
const onLabels = (labels) => {
  if (labels.includes('question')) say(owners(), 'has a question for you:');
  if (labels.includes('e2e:red')) say([...new Set([...assignees(), ...owners()])], 'E2E is red:');
};

if (action === 'created') {
  if ((data.assignees || []).length) say(assignees(), 'new task for you:');
  onLabels((data.labels || []).map((l) => l.name));
} else if (action === 'updated') {
  const f = activity.field;
  if (f === 'assignee_ids' || f === 'assignees') {
    const ids = added(activity.old_value, activity.new_value);
    const who = (data.assignees || []).filter((a) => ids.includes(a.id)).map((a) => personByEmail(a.email) || { name: a.display_name || a.email });
    if (who.length) say(who, `assigned to you${actor ? ' by ' + actor : ''}:`);
  } else if (f === 'label_ids' || f === 'labels') {
    onLabels(added(activity.old_value, activity.new_value).map(labelName).filter(Boolean));
  } else if (f === 'state_id' || f === 'state') {
    const state = STATES[activity.new_value];
    if (state === 'In Review') say(reviewers(), 'ready for review:');
    if (state === 'Ready for E2E' || state === 'Done') say([], `→ ${state}${actor ? ' (by ' + actor + ')' : ''}:`);
  }
}
return msgs.map((m) => ({ json: m }));
