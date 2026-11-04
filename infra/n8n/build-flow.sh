#!/usr/bin/env bash
# Builds infra/n8n/flows/plane-notify.json from plane-notify.js (Mattermost channel), dm-collect.js and
# dm-flush.js (private Telegram messages) + the repo's config (people.yaml, routing.yaml,
# projects.yaml) + the projects' state ids, and with --import loads it into the running n8n
# (replacing the flow with the same id). Re-run after editing people.yaml (handles, chat ids),
# routing.yaml (reviewers, question owners) or the JS. Host-side only (needs uv, jq, docker).
# "Wake triage" also POSTs every event to the wake service on the host (infra/macos/wake.py), which
# starts the triage agent within seconds (it fails silently, so notifications never depend on it).
#
#   PLANE_API_KEY="$TRACKER_ADMIN_TOKEN" bash infra/n8n/build-flow.sh [--import]
#
# Settings (environment; the agents' env file fills the TRACKER_* ones when they're unset):
#   TRACKER_BASE_URL     Plane as n8n reaches it (API calls)
#   PLANE_WEB_URL        Plane as people open it (links in messages; default TRACKER_BASE_URL)
#   TRACKER_WORKSPACE    the workspace slug
#   NOTIFY_TZ            time zone of the quiet hours and the times in messages (default UTC)
#   QUIET_FROM / QUIET_TO  quiet hours, whole hours (default 22 / 8; equal values = no quiet hours)
#   TG_TOPICS            JSON project key → Telegram topic id for channel posts (default {})
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ENV_FILE="${AGENT_OPS_ENV:-$HOME/.config/agent-ops/env}"
envval() { [ -r "$ENV_FILE" ] && grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '"' || true; }
PLANE_URL="${TRACKER_BASE_URL:-$(envval TRACKER_BASE_URL)}"
WORKSPACE="${TRACKER_WORKSPACE:-$(envval TRACKER_WORKSPACE)}"
WEB_URL="${PLANE_WEB_URL:-$PLANE_URL}"
[ -n "$PLANE_URL" ] && [ -n "$WORKSPACE" ] || { echo "set TRACKER_BASE_URL and TRACKER_WORKSPACE" >&2; exit 1; }
CONTAINER="${N8N_CONTAINER:-n8n-n8n-1}"
TG_TOPICS="${TG_TOPICS:-}"; [ -n "$TG_TOPICS" ] || TG_TOPICS='{}'
: "${PLANE_API_KEY:?set PLANE_API_KEY (admin token) to read the project states}"

# people, projects (task-board id → key) and the flows' CONFIG from the repo's YAML files.
cfg="$(uv run -q --no-project --with pyyaml==6.0.2 python - "$ROOT" "$WEB_URL" "$WORKSPACE" <<'PY'
import json, os, sys, yaml
root, web_url, ws = sys.argv[1:4]
load = lambda n: (yaml.safe_load(open(os.path.join(root, n))) or {}) if os.path.exists(os.path.join(root, n)) else {}
pf = "people.yaml" if os.path.exists(os.path.join(root, "people.yaml")) else "people.template.yaml"
people = {k: {"name": (v or {}).get("full_name") or k.capitalize(), "email": str((v or {}).get("email") or ""),
              "telegram": str((v or {}).get("telegram_username") or "").lstrip("@"),
              "mattermost": str((v or {}).get("mattermost_username") or "").lstrip("@"),
              "chat_id": str((v or {}).get("telegram_chat_id") or "").strip()}
          for k, v in (load(pf).get("people") or {}).items()}
projects = {str(p["tracker_project_id"]): k for k, p in (load("projects.yaml").get("projects") or {}).items()
            if p and p.get("tracker_project_id")}
r = load("routing.yaml")
role = lambda d: {"reviewers": list(d.get("approvers") or r.get("approvers") or []),
                  "questions": [x for x in [d.get("question_owner") or r.get("question_owner")] if x]}
roles = {"default": role({})}
for k, p in (r.get("projects") or {}).items():
    if p and (p.get("approvers") or p.get("question_owner")):
        roles[k] = role(p)
config = {"web_url": web_url.rstrip("/"), "workspace": ws, "tz": os.environ.get("NOTIFY_TZ", "UTC"),
          "quiet_from": int(os.environ.get("QUIET_FROM", 22)), "quiet_to": int(os.environ.get("QUIET_TO", 8)),
          "roles": roles}
print(json.dumps({"people": people, "projects": projects, "config": config}))
PY
)"
people="$(jq -c .people <<<"$cfg")"
PROJECTS="$(jq -c .projects <<<"$cfg")"
CONFIG="$(jq -c .config <<<"$cfg")"
states='{}'
for pid in $(jq -r 'keys[]' <<<"$PROJECTS"); do
  states="$(curl -fsS -H "X-API-Key: $PLANE_API_KEY" "${PLANE_URL%/}/api/v1/workspaces/$WORKSPACE/projects/$pid/states/" \
    | jq -c --argjson acc "$states" '$acc + ([(if type == "object" then .results else . end)[] | {(.id): .name}] | add)')"
done

cred_id=""
if docker exec "$CONTAINER" true 2>/dev/null; then
  docker exec "$CONTAINER" n8n export:credentials --all --output=/tmp/creds.json >/dev/null 2>&1 || true
  cred_id="$(docker exec "$CONTAINER" sh -c 'cat /tmp/creds.json 2>/dev/null; rm -f /tmp/creds.json' \
    | jq -r '.[]? | select(.name == "agent-ops-telegram") | .id')"
fi
[ -n "$cred_id" ] || echo "[n8n] warning: credential agent-ops-telegram not found; pick it in the Telegram node after import" >&2

code="$(jq -Rrs --arg pr "$PROJECTS" --arg t "$TG_TOPICS" --arg s "$states" --arg p "$people" --arg c "$CONFIG" \
  'sub("__PROJECTS__"; $pr) | sub("__TOPICS__"; $t) | sub("__STATES__"; $s) | sub("__PEOPLE__"; $p) | sub("__CONFIG__"; $c)' "$HERE/flows/plane-notify.js")"
embed() {  # embed <file>: fill the placeholders of a Code node source
  jq -Rrs --arg pr "$PROJECTS" --arg p "$people" --arg c "$CONFIG" --arg api "$(jq -n --arg u "${PLANE_URL%/}" '$u')" \
    'sub("__PROJECTS__"; $pr) | sub("__PEOPLE__"; $p) | sub("__CONFIG__"; $c) | sub("__PLANE_API__"; $api)' "$1"
}
collect="$(embed "$HERE/flows/dm-collect.js")"
flush="$(embed "$HERE/flows/dm-flush.js")"
jq -n --arg code "$code" --arg collect "$collect" --arg flush "$flush" --arg cred "$cred_id" '
  (if $cred == "" then {} else { telegramApi: { id: $cred, name: "agent-ops-telegram" } } end) as $tg | {
  id: "planeNotify0001", name: "plane-notify", active: false,
  settings: { executionOrder: "v1" },
  nodes: [
    { id: "a1", name: "Plane webhook", type: "n8n-nodes-base.webhook", typeVersion: 2, position: [0, 0],
      webhookId: "4f6b2c1e-plane-notify", parameters: { httpMethod: "POST", path: "plane", responseMode: "onReceived", options: {} } },
    { id: "a2", name: "Route event", type: "n8n-nodes-base.code", typeVersion: 2, position: [240, 0],
      parameters: { jsCode: $code } },
    { id: "a4", name: "Mattermost", type: "n8n-nodes-base.httpRequest", typeVersion: 4.2, position: [480, 100],
      parameters: { method: "POST", url: "={{ $env.MATTERMOST_WEBHOOK_URL }}", sendBody: true, specifyBody: "json",
        jsonBody: "={{ JSON.stringify({ text: $json.mm }) }}", options: {} } },
    { id: "a5", name: "Wake triage", type: "n8n-nodes-base.httpRequest", typeVersion: 4.2, position: [240, 200],
      onError: "continueRegularOutput", retryOnFail: false,
      parameters: { method: "POST", url: "http://host.docker.internal:8766/wake/triage",
        sendHeaders: true, headerParameters: { parameters: [{ name: "X-Wake-Token", value: "={{ $env.WAKE_TOKEN }}" }] },
        options: { timeout: 5000 } } },
    # Private messages (dm-collect.js / dm-flush.js; README "Private messages").
    { id: "b1", name: "Collect DMs", type: "n8n-nodes-base.code", typeVersion: 2, position: [240, 400],
      parameters: { jsCode: $collect } },
    { id: "b2", name: "Every minute", type: "n8n-nodes-base.scheduleTrigger", typeVersion: 1.2, position: [0, 600],
      parameters: { rule: { interval: [{ field: "minutes", minutesInterval: 1 }] } } },
    { id: "b3", name: "Flush DMs", type: "n8n-nodes-base.code", typeVersion: 2, position: [240, 600],
      parameters: { jsCode: $flush } },
    { id: "b4", name: "Telegram DM?", type: "n8n-nodes-base.if", typeVersion: 2.2, position: [480, 520],
      parameters: { conditions: { options: { caseSensitive: true, leftValue: "", typeValidation: "loose", version: 2 },
        conditions: [{ id: "dm-tg", leftValue: "={{ $json.kind }}", rightValue: "telegram",
          operator: { type: "string", operation: "equals" } }], combinator: "and" }, options: {} } },
    { id: "b5", name: "Send DM (Telegram)", type: "n8n-nodes-base.telegram", typeVersion: 1.2, position: [720, 520],
      onError: "continueRegularOutput",
      parameters: { chatId: "={{ $json.chat_id }}", text: "={{ $json.text }}",
        additionalFields: { appendAttribution: false, disable_web_page_preview: true, parse_mode: "HTML" } },
      credentials: $tg },
    { id: "b6", name: "Mattermost DMs on?", type: "n8n-nodes-base.if", typeVersion: 2.2, position: [480, 700],
      parameters: { conditions: { options: { caseSensitive: true, leftValue: "", typeValidation: "loose", version: 2 },
        conditions: [
          { id: "dm-mm-on", leftValue: "={{ $env.MM_DM_ENABLED }}", rightValue: "true", operator: { type: "string", operation: "equals" } },
          { id: "dm-mm-kind", leftValue: "={{ $json.kind }}", rightValue: "mattermost", operator: { type: "string", operation: "equals" } }
        ], combinator: "and" }, options: {} } },
    { id: "b7", name: "Send DM (Mattermost)", type: "n8n-nodes-base.httpRequest", typeVersion: 4.2, position: [720, 700],
      onError: "continueRegularOutput",
      parameters: { method: "POST", url: "={{ $env.MATTERMOST_WEBHOOK_URL }}", sendBody: true, specifyBody: "json",
        jsonBody: "={{ JSON.stringify({ channel: $json.channel, text: $json.text }) }}", options: { timeout: 10000 } } }
  ],
  connections: {
    "Plane webhook": { main: [[{ node: "Route event", type: "main", index: 0 }, { node: "Wake triage", type: "main", index: 0 },
      { node: "Collect DMs", type: "main", index: 0 }]] },
    "Route event": { main: [[{ node: "Mattermost", type: "main", index: 0 }]] },
    "Every minute": { main: [[{ node: "Flush DMs", type: "main", index: 0 }]] },
    "Flush DMs": { main: [[{ node: "Telegram DM?", type: "main", index: 0 }, { node: "Mattermost DMs on?", type: "main", index: 0 }]] },
    "Telegram DM?": { main: [[{ node: "Send DM (Telegram)", type: "main", index: 0 }], []] },
    "Mattermost DMs on?": { main: [[{ node: "Send DM (Mattermost)", type: "main", index: 0 }], []] }
  }
}' > "$HERE/flows/plane-notify.json"
echo "[n8n] built flows/plane-notify.json ($(jq 'length' <<<"$people") people, $(jq 'length' <<<"$PROJECTS") projects)"

if [ "${1:-}" = "--import" ]; then
  docker cp "$HERE/flows/plane-notify.json" "$CONTAINER:/tmp/plane-notify.json"
  docker exec "$CONTAINER" n8n import:workflow --input=/tmp/plane-notify.json
  docker exec -u root "$CONTAINER" rm -f /tmp/plane-notify.json
  # Import deactivates the flow (notifications and instant triage stop). Publish it again and
  # restart n8n, which only picks up a CLI publish on start (a few seconds of downtime).
  docker exec "$CONTAINER" n8n publish:workflow --id=planeNotify0001 >/dev/null
  docker restart "$CONTAINER" >/dev/null
  for _ in $(seq 1 30); do curl -fs -o /dev/null http://localhost:5678/healthz && break; sleep 2; done
  docker exec "$CONTAINER" n8n list:workflow --active=true 2>/dev/null | grep -q '^planeNotify0001|' \
    && echo "[n8n] imported and published; n8n restarted" \
    || { echo "[n8n] imported, but the flow is not active: publish it in the editor" >&2; exit 1; }
fi
