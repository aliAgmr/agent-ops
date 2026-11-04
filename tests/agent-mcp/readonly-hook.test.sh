#!/usr/bin/env bash
# Tests for service/agent-mcp/.claude/hooks/readonly.sh. Run: bash tests/agent-mcp/readonly-hook.test.sh
set -u
HOOK="$(cd "$(dirname "$0")/../.." && pwd)/service/agent-mcp/.claude/hooks/readonly.sh"
fail=0
check() {  # check <expected-exit> <label> <json>
  printf '%s' "$3" | "$HOOK" >/dev/null 2>&1; got=$?
  if [ "$got" = "$1" ]; then echo "ok   $2"; else echo "FAIL $2 (expected $1, got $got)"; fail=1; fi
}
check 0 "plane list"              '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"list","project_id":"x"}}'
check 0 "plane retrieve_by_id"    '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"retrieve_by_identifier","workitem_identifier":"BIL-1"}}'
check 0 "plane search"            '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"search","query":"x"}}'
check 0 "plane member list"       '{"tool_name":"mcp__plane__member","tool_input":{"action":"list_project"}}'
check 2 "plane create"            '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"create","name":"x"}}'
check 2 "plane update"            '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"update","workitem_id":"x"}}'
check 2 "plane comment create"    '{"tool_name":"mcp__plane__workitem_comment","tool_input":{"action":"create","comment_html":"x"}}'
check 2 "plane manage_label"      '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"manage_label"}}'
check 2 "plane tool w/o action"   '{"tool_name":"mcp__plane__create_work_item","tool_input":{}}'
check 0 "plane get_ w/o action"   '{"tool_name":"mcp__plane__get_me","tool_input":{}}'
check 0 "read repo file"          '{"tool_name":"Read","tool_input":{"file_path":"/srv/repos/billing-backend/package.json"}}'
check 0 "read skill"              '{"tool_name":"Read","tool_input":{"file_path":"/srv/agent-ops/plugins/core/skills/warehouse-traps/SKILL.md"}}'
check 2 "read env file"           '{"tool_name":"Read","tool_input":{"file_path":"'"$HOME"'/.config/agent-ops/env"}}'
check 2 "read env via ~"          '{"tool_name":"Read","tool_input":{"file_path":"~/.config/agent-ops/env"}}'
check 2 "read env via .."         '{"tool_name":"Read","tool_input":{"file_path":"/srv/repos/../../.config/agent-ops/env"}}'
check 2 "read token file"         '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x/plane-admin.token"}}'
check 2 "read plane.env"          '{"tool_name":"Read","tool_input":{"file_path":"'"$HOME"'/plane-selfhost/plane-app/plane.env"}}'
check 2 "read ssh key"            '{"tool_name":"Read","tool_input":{"file_path":"'"$HOME"'/.ssh/id_ed25519"}}'
check 2 "grep in config"          '{"tool_name":"Grep","tool_input":{"pattern":"TOKEN","path":"'"$HOME"'/.config/agent-ops"}}'
check 2 "glob token files"        '{"tool_name":"Glob","tool_input":{"pattern":"**/*.token"}}'
check 2 "read agent-mcp db"       '{"tool_name":"Read","tool_input":{"file_path":"'"$HOME"'/.local/share/agent-mcp/agent-mcp.sqlite3"}}'
check 0 "bash passes through"     '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x grep -n foo"}}'
[ "$fail" = 0 ] && echo "all passed" || { echo "FAILURES"; exit 1; }
