#!/usr/bin/env bash
# Creates or updates team projects in Plane: states, labels, members, lead. Idempotent: safe to run
# again; it only adds what is missing and never deletes anything.
#
#   PLANE_API_KEY=... bash infra/plane/configure.sh <plane-url> <workspace-slug>          # every project in projects.yaml
#   PLANE_PROJECT_CODE=BIL bash infra/plane/configure.sh <plane-url> <workspace-slug>     # one of them
#   e.g. PLANE_API_KEY="$TRACKER_ADMIN_TOKEN" bash infra/plane/configure.sh https://plane.example.com acme
#
# Per project, from the repo's config (override any of them with the variable in brackets):
#   name and code      projects.yaml key and `name`              [PLANE_PROJECT_NAME, PLANE_PROJECT_CODE]
#   members            everyone routing.yaml puts in the project (all of people.yaml for the default
#                      project) + the bot users in people.yaml `service:`             [PLANE_PROJECT_MEMBERS]
#   admins             the project's approvers (routing.yaml)                         [PLANE_PROJECT_ADMINS]
#   lead               the project's question owner (routing.yaml)                    [PLANE_PROJECT_LEAD]
# Emails come from people.yaml (falling back to the example people.template.yaml).
#
# The workspace itself, saved views and bot users are not in Plane's public API: create those by hand
# (see README.md). The token is an admin's personal access token; it is read from the environment only.
set -euo pipefail

PLANE_URL="${1:-${PLANE_URL:-${TRACKER_BASE_URL:-}}}"
WS="${2:-${PLANE_WORKSPACE:-${TRACKER_WORKSPACE:-}}}"
[ -n "$PLANE_URL" ] && [ -n "$WS" ] || { echo "usage: configure.sh <plane-url> <workspace-slug>" >&2; exit 1; }
: "${PLANE_API_KEY:?set PLANE_API_KEY to an admin personal access token}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
API="${PLANE_URL%/}/api/v1/workspaces/$WS"

# Without a project: run once per project in projects.yaml (keys in file order).
if [ -z "${PLANE_PROJECT_CODE:-}" ]; then
  codes="$(uv run -q --no-project --with pyyaml==6.0.2 python -c 'import sys, yaml; print(" ".join((yaml.safe_load(open(sys.argv[1])) or {}).get("projects", {})))' "$REPO/projects.yaml")"
  for code in $codes; do PLANE_PROJECT_CODE="$code" bash "$0" "$PLANE_URL" "$WS"; done
  exit 0
fi
PROJECT_ID_CODE="$PLANE_PROJECT_CODE"

# Name, members, admins and lead for this project from projects.yaml / routing.yaml / people.yaml.
derived="$(uv run -q --no-project --with pyyaml==6.0.2 python - "$REPO" "$PROJECT_ID_CODE" <<'PY'
import os, sys, yaml
repo, code = sys.argv[1], sys.argv[2]
load = lambda n: (yaml.safe_load(open(os.path.join(repo, n))) or {}) if os.path.exists(os.path.join(repo, n)) else {}
projects = load("projects.yaml").get("projects") or {}
routing = load("routing.yaml")
pf = "people.yaml" if os.path.exists(os.path.join(repo, "people.yaml")) else "people.template.yaml"
pdoc = load(pf)
people = pdoc.get("people") or {}
service = pdoc.get("service") or {}
email = lambda k: str((people.get(k) or {}).get("email") or "").strip()
first = next(iter(projects), None)
proj = (routing.get("projects") or {}).get(code) or {}
if code == first or not proj.get("teams"):
    keys = list(people)                                     # the default project: the whole team
else:
    keys = []
    for t in (proj.get("teams") or {}).values():
        t = t or {}
        keys += t.get("people") or ([t["person"]] if t.get("person") else [])
bots = [str(v).strip() for k, v in service.items() if k.startswith("tracker_") and k.endswith("_bot") and v]
approvers = proj.get("approvers") or routing.get("approvers") or []
lead = proj.get("question_owner") or routing.get("question_owner") or ""
print("NAME=" + str((projects.get(code) or {}).get("name") or code))
print("MEMBERS=" + ",".join(dict.fromkeys([e for e in map(email, keys) if e] + bots)))
print("ADMINS=" + ",".join(e for e in map(email, approvers) if e))
print("LEAD=" + email(lead))
PY
)"
val() { sed -n "s/^$1=//p" <<<"$derived"; }
PROJECT_NAME="${PLANE_PROJECT_NAME:-$(val NAME)}"
ADMINS="${PLANE_PROJECT_ADMINS:-$(val ADMINS)}"
MEMBERS="${PLANE_PROJECT_MEMBERS:-$(val MEMBERS)}"
PLANE_PROJECT_LEAD="${PLANE_PROJECT_LEAD:-$(val LEAD)}"

log() { echo "[plane] $*"; }
api() {  # method path [json]
  local out code
  out="$(curl -sS -w '\n%{http_code}' -X "$1" "$API$2" -H "X-API-Key: $PLANE_API_KEY" \
    -H 'Content-Type: application/json' ${3:+--data "$3"})"
  code="${out##*$'\n'}"; out="${out%$'\n'*}"
  [[ "$code" == 2* ]] || { echo "[plane] $1 $2 → HTTP $code: $out" >&2; exit 1; }
  printf '%s' "$out"
}
results() { jq -c 'if type == "object" and has("results") then .results else . end'; }

# 1. Project
project_id="$(api GET /projects/ | results | jq -r --arg n "$PROJECT_NAME" '.[] | select(.name == $n) | .id' | head -1)"
if [ -z "$project_id" ]; then
  log "creating project $PROJECT_NAME ($PROJECT_ID_CODE)"
  project_id="$(api POST /projects/ "$(jq -nc --arg n "$PROJECT_NAME" --arg i "$PROJECT_ID_CODE" \
    '{name: $n, identifier: $i, description: "\($n) team board. Agents and people talk through work items and comments here."}')" | jq -r .id)"
else
  log "project exists: $project_id"
fi
P="/projects/$project_id"
# Saved views are off by default; the team views in README.md need them.
# Projects must be private (people can't join on their own). The public API ignores "network", so
# set it in the UI (project settings → General → Private) or with the Django shell (README.md).
api PATCH "$P/" '{"issue_views_view": true}' >/dev/null

# 2. States: name|group|color. Plane's default "Backlog" is renamed to Inbox and made the default,
# so new work items land in Inbox, where the triage agent picks them up. "Backlog" (added after the
# rename) holds parked ideas: triage only reviews them; someone moves them to Inbox when it's time.
STATES=(
  "Backlog|backlog|#CBD5E1"
  "Inbox|backlog|#A3A3A3"
  "Triaged|unstarted|#8B5CF6"
  "Todo|unstarted|#3F76FF"
  "In Progress|started|#F59E0B"
  "In Review|started|#0EA5E9"
  "Ready for E2E|started|#14B8A6"
  "Blocked|started|#EF4444"
  "Done|completed|#16A34A"
  "Cancelled|cancelled|#6B7280"
)
# "Findings" (every project): problems the nightly agent found (security, tests, data health,
# untracked follow-up work). Unassigned; an approver moves each one into Todo or cancels it.
STATES=("${STATES[0]}" "Findings|backlog|#FB923C" "${STATES[@]:1}")
states="$(api GET "$P/states/" | results)"
backlog_id="$(jq -r '.[] | select(.name == "Backlog") | .id' <<<"$states")"
if [ -n "$backlog_id" ] && ! jq -e '.[] | select(.name == "Inbox")' <<<"$states" >/dev/null; then
  log "renaming Backlog → Inbox"
  api PATCH "$P/states/$backlog_id/" '{"name": "Inbox"}' >/dev/null
  states="$(api GET "$P/states/" | results)"
fi
for s in "${STATES[@]}"; do
  IFS='|' read -r name group color <<<"$s"
  if ! jq -e --arg n "$name" '.[] | select(.name == $n)' <<<"$states" >/dev/null; then
    log "state + $name ($group)"
    api POST "$P/states/" "$(jq -nc --arg n "$name" --arg g "$group" --arg c "$color" '{name: $n, group: $g, color: $c}')" >/dev/null
  fi
done
states="$(api GET "$P/states/" | results)"
# Board order follows the list above.
seq=0
for s in "${STATES[@]}"; do
  name="${s%%|*}"; seq=$((seq + 10000))
  read -r id cur < <(jq -r --arg n "$name" '.[] | select(.name == $n) | "\(.id) \(.sequence)"' <<<"$states")
  if [ "${cur%.*}" != "$seq" ]; then
    api PATCH "$P/states/$id/" "{\"sequence\": $seq}" >/dev/null
  fi
done
states="$(api GET "$P/states/" | results)"
inbox_id="$(jq -r '.[] | select(.name == "Inbox") | .id' <<<"$states")"
if [ "$(jq -r '.[] | select(.name == "Inbox") | .default' <<<"$states")" != true ]; then
  log "making Inbox the default state"
  api PATCH "$P/states/$inbox_id/" '{"default": true}' >/dev/null
fi

# 3. Labels: name|color
LABELS=(
  "team:product|#F97316" "team:design|#EC4899" "team:backend|#3F76FF" "team:frontend|#14B8A6" "team:data|#8B5CF6" "team:devops|#64748B"
  "question|#0EA5E9" "e2e:red|#EF4444" "e2e:green|#16A34A"
  "source:agent|#6B7280" "source:human|#A3A3A3"
  "lesson|#F43F5E" "directive|#7C3AED"
  "source:nightly|#FB923C" "out-of-pipeline|#64748B"
  "out-of-scope|#9CA3AF"     # a declined request; its [decision] comment says why (task-protocol)
  # effort (set by triage, exactly one per assessed item): xs <2h, s up to 1 day, m 1–3 days, l 3 days–1 week, xl >1 week
  "effort:xs|#BBF7D0" "effort:s|#86EFAC" "effort:m|#FDE047" "effort:l|#FDBA74" "effort:xl|#F87171"
  # deep analysis before routing (bugs and features of effort s+): triage queues, the analyst agent writes it
  "analysis:needed|#A78BFA" "analysis:done|#6D28D9"
)
labels="$(api GET "$P/labels/" | results)"
for l in "${LABELS[@]}"; do
  IFS='|' read -r name color <<<"$l"
  if ! jq -e --arg n "$name" '.[] | select(.name == $n)' <<<"$labels" >/dev/null; then
    log "label + $name"
    api POST "$P/labels/" "$(jq -nc --arg n "$name" --arg c "$color" '{name: $n, color: $c}')" >/dev/null
  fi
done

# 4. Project members: workspace members (by email) that must be in the project. Approvers
# (routing.yaml) and workspace Admins join as Admin (20), everyone else as Member (15).
# Note: removing someone in the UI only deactivates the membership; the API can't re-add it, so
# re-add removed people in the UI (project settings → Members → Add member).
# People are skipped until they accepted their workspace invite; re-run this after each one joins.
ws_members="$(api GET /members/ | results)"
project_members="$(api GET "$P/members/" | results)"
IFS=',' read -ra emails <<<"$MEMBERS"
[ -n "$MEMBERS" ] || emails=()
for email in ${emails[@]+"${emails[@]}"}; do
  uid="$(jq -r --arg e "$email" '.[] | select(.email == $e) | .id' <<<"$ws_members")"
  if [ -z "$uid" ]; then
    log "skip $email: not a workspace member yet (invite pending or not sent)"
  elif ! jq -e --arg u "$uid" '.[] | select(.id == $u or .member == $u)' <<<"$project_members" >/dev/null; then
    # Plane expects workspace Admins to be project Admins too (its UI locks any other role).
    ws_role="$(jq -r --arg e "$email" '.[] | select(.email == $e) | .role // 15' <<<"$ws_members")"
    role=15; { [[ ",$ADMINS," == *",$email,"* ]] || [ "$ws_role" = 20 ]; } && role=20
    log "project member + $email (role $role)"
    api POST "$P/members/" "$(jq -nc --arg u "$uid" --argjson r "$role" '{member: $u, role: $r}')" >/dev/null
  fi
done

# 5. Project lead (by email): the project's owner in Plane (routing.yaml question owner).
if [ -n "${PLANE_PROJECT_LEAD:-}" ]; then
  lead="$(jq -r --arg e "$PLANE_PROJECT_LEAD" '.[] | select(.email == $e) | .id' <<<"$ws_members")"
  if [ -z "$lead" ]; then
    log "skip lead $PLANE_PROJECT_LEAD: not a workspace member"
  elif [ "$(api GET "$P/" | jq -r '.project_lead // ""')" != "$lead" ]; then
    log "project lead = $PLANE_PROJECT_LEAD"
    api PATCH "$P/" "$(jq -nc --arg u "$lead" '{project_lead: $u}')" >/dev/null
  fi
fi

log "done: $PLANE_URL/$WS/projects/$project_id/issues"
