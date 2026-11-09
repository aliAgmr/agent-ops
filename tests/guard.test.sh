#!/usr/bin/env bash
# Tests for plugins/core/scripts/guard.sh. Run: bash tests/guard.test.sh
set -u
GUARD="$(cd "$(dirname "$0")/.." && pwd)/plugins/core/scripts/guard.sh"
fail=0
check() {  # check <expected-exit> <label> <json>
  printf '%s' "$3" | "$GUARD" >/dev/null 2>&1; got=$?
  if [ "$got" = "$1" ]; then echo "ok   $2"; else echo "FAIL $2 (expected $1, got $got)"; fail=1; fi
}
check_role() {  # check_role <role> <expected-exit> <label> <json>  (as the core plugin hook calls it)
  printf '%s' "$4" | "$GUARD" --role "$1" >/dev/null 2>&1; got=$?
  if [ "$got" = "$2" ]; then echo "ok   [$1] $3"; else echo "FAIL [$1] $3 (expected $2, got $got)"; fail=1; fi
}

check 2 "jenkins trigger"          '{"tool_name":"mcp__jenkins-mcp__triggerBuild","tool_input":{}}'
check 0 "jenkins read log"         '{"tool_name":"mcp__jenkins__getBuildLog","tool_input":{}}'
check 2 "clickhouse insert"        '{"tool_name":"mcp__plugin_clickhouse-tools_clickhouse__run_query","tool_input":{"query":"-- Claude user: x\nINSERT INTO t SELECT 1"}}'
check 2 "clickhouse alter"         '{"tool_name":"mcp__mcp-clickhouse__run_query","tool_input":{"query":"ALTER TABLE analytics.x DELETE WHERE 1"}}'
check 0 "clickhouse select"        '{"tool_name":"mcp__clickhouse__run_query","tool_input":{"query":"-- Claude user: x\nselect updated_at from t where true"}}'
check 0 "select with update word"  '{"tool_name":"mcp__metabase__run_sql","tool_input":{"query":"select last_update from x where updated = 1"}}'
check 2 "postgres modify"          '{"tool_name":"mcp__postgres__postgres_mcp_modify","tool_input":{}}'
check 2 "postgres delete via query" '{"tool_name":"mcp__postgres__postgres_mcp_query","tool_input":{"sql":"DELETE FROM fund"}}'
check 2 "postgres DDL, renamed tool"  '{"tool_name":"mcp__postgres__query","tool_input":{"query":"CREATE TABLE public_old.x (a int)"}}'
check 2 "clickhouse insert, any tool" '{"tool_name":"mcp__clickhouse__run_select_query","tool_input":{"query":"INSERT INTO t VALUES (1)"}}'
check 0 "postgres select, renamed tool" '{"tool_name":"mcp__postgres__query","tool_input":{"query":"SELECT count(*) FROM fund"}}'
# SQL keywords inside quoted string literals are data; real statements around them are not.
check 0 "ch query_log search for DDL text" '{"tool_name":"mcp__clickhouse__run_query","tool_input":{"query":"SELECT max(event_time) FROM system.query_log WHERE query ILIKE '"'"'%create or replace table analytics.calendar_daily%'"'"'"}}'
check 0 "pg search for 'delete from' text" '{"tool_name":"mcp__postgres__postgres_mcp_query","tool_input":{"query":"SELECT pid FROM pg_stat_activity WHERE query LIKE '"'"'%delete from fund%'"'"'"}}'
check 0 "literal with '' escape"          '{"tool_name":"mcp__clickhouse__run_query","tool_input":{"query":"SELECT '"'"'it'"''"'s a drop table note'"'"' AS x"}}'
check 0 "literal with backslash escape"   '{"tool_name":"mcp__clickhouse__run_query","tool_input":{"query":"SELECT '"'"'a\\'"'"' insert into t'"'"' AS x"}}'
check 2 "write after a literal"           '{"tool_name":"mcp__clickhouse__run_query","tool_input":{"query":"SELECT '"'"'x'"'"'; DROP TABLE t"}}'
check 2 "write between literals"          '{"tool_name":"mcp__clickhouse__run_query","tool_input":{"query":"SELECT '"'"'a'"'"' CREATE TABLE t (a Int8) '"'"'b'"'"'"}}'
check 2 "insert with literal values"      '{"tool_name":"mcp__postgres__postgres_mcp_query","tool_input":{"query":"INSERT INTO t VALUES ('"'"'select'"'"')"}}'
check 2 "update setting a literal"        '{"tool_name":"mcp__postgres__postgres_mcp_query","tool_input":{"query":"UPDATE fund SET note = '"'"'x'"'"'"}}'
check 2 "unclosed quote can't hide a write" '{"tool_name":"mcp__clickhouse__run_query","tool_input":{"query":"SELECT '"'"'x; CREATE TABLE t (a Int8)"}}'
check 2 "gitlab merge"             '{"tool_name":"mcp__gitlab__merge_merge_request","tool_input":{}}'
check 2 "gitlab play job"          '{"tool_name":"mcp__gitlab__play_pipeline_job","tool_input":{}}'
check 0 "gitlab create MR"         '{"tool_name":"mcp__gitlab__create_merge_request","tool_input":{}}'
check 2 "plane delete"             '{"tool_name":"mcp__plugin_core_plane__workitem","tool_input":{"action":"delete","id":"1"}}'
check 0 "plane update"             '{"tool_name":"mcp__plugin_core_plane__workitem","tool_input":{"action":"update"}}'
# Dry runs: AGENT_PLANE_READ_ONLY=1 makes Plane read-only (the env can only tighten the guard)
export AGENT_PLANE_READ_ONLY=1
check 2 "dry run: plane create"     '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"create","name":"x"}}'
check 2 "dry run: plane update"     '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"update","id":"1"}}'
check 2 "dry run: plane comment"    '{"tool_name":"mcp__plane__workitem_comment","tool_input":{"action":"create"}}'
check 0 "dry run: plane list"       '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"list","project_id":"p"}}'
check 0 "dry run: plane retrieve"   '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"retrieve","id":"1"}}'
check 0 "dry run: activity list"    '{"tool_name":"mcp__plane__workitem_activity","tool_input":{"action":"list"}}'
unset AGENT_PLANE_READ_ONLY
export AGENT_TRACKER_READ_ONLY=1
check 2 "dry run (tracker switch): plane update" '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"update","id":"1"}}'
check 0 "dry run (tracker switch): plane list"   '{"tool_name":"mcp__plane__workitem","tool_input":{"action":"list","project_id":"p"}}'
unset AGENT_TRACKER_READ_ONLY
check 2 "psql"                     '{"tool_name":"Bash","tool_input":{"command":"psql $URL -c \"select 1\""}}'
check 2 "clickhouse-client"        '{"tool_name":"Bash","tool_input":{"command":"clickhouse-client --query \"select 1\""}}'
check 2 "push main"                '{"tool_name":"Bash","tool_input":{"command":"git push origin main"}}'
check 2 "push HEAD:main"           '{"tool_name":"Bash","tool_input":{"command":"git push origin HEAD:main"}}'
check 0 "push feature"             '{"tool_name":"Bash","tool_input":{"command":"git push -u origin feature/maintenance-page"}}'
check 2 "force push"               '{"tool_name":"Bash","tool_input":{"command":"git push --force origin x"}}'
check 2 "jenkins curl build"       '{"tool_name":"Bash","tool_input":{"command":"curl -X POST https://jenkins.x/job/billing-prod/build"}}'
check 0 "plain bash"               '{"tool_name":"Bash","tool_input":{"command":"bun run lint"}}'

check 0 "repo git grep"            '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/billing-backend grep -n -i resolveLanguage -- src"}}'
check 0 "repo git log"             '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/billing-frontend log --oneline -5"}}'
check 0 "repo git grep -o -c"      '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x grep -o -c TODO"}}'
check 2 "repo git grep -O pager"   '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x grep -n -O'sh -c id' foo"}}'
check 2 "repo git grep -nO bundle" '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x grep -nOvim foo"}}'
check 2 "repo git open-files"      '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x grep --open-files-in-pager=sh foo"}}'
check 2 "repo git -c alias"        '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x -c alias.g=!id g"}}'
check 2 "repo git -c before grep"  '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x -c core.fsmonitor=id grep foo"}}'
check 2 "repo git checkout"        '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x checkout -- . grep foo"}}'
check 2 "repo git no-index"        '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x grep --no-index TOKEN /etc/hosts"}}'
check 2 "repo git log --output"    '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x log --output=/tmp/x"}}'
check 2 "repo second git hidden"   '{"tool_name":"Bash","tool_input":{"command":"git -C /srv/repos/x grep foo && git -C /srv/repos/x config core.pager id"}}'

# DevOps role exemption: non-production Jenkins and pipeline runs only. Everyone else stays strict.
J_STAGE='{"cwd":"/home/x/main","tool_name":"mcp__plugin_jenkins-tools_jenkins__triggerBuild","tool_input":{"jobFullName":"pricing/backend-stage","parameters":{"BRANCH":"dev"}}}'
J_PROD='{"tool_name":"mcp__plugin_jenkins-tools_jenkins__triggerBuild","tool_input":{"jobFullName":"pricing/backend-prod"}}'
J_PRODPARAM='{"tool_name":"mcp__plugin_jenkins-tools_jenkins__rebuildBuild","tool_input":{"jobFullName":"billing-deploy","parameters":{"ENV":"Production"}}}'
J_MAINPARAM='{"tool_name":"mcp__jenkins__replayBuild","tool_input":{"jobFullName":"billing-api","parameters":{"BRANCH":"main"}}}'
J_RELEASE='{"tool_name":"mcp__jenkins__triggerBuild","tool_input":{"jobFullName":"billing/Release-train"}}'
J_PRODUCTS='{"tool_name":"mcp__jenkins__triggerBuild","tool_input":{"jobFullName":"products-sync-dev"}}'
P_DEV='{"tool_name":"mcp__plugin_gitlab-tools_gitlab__play_pipeline_job","tool_input":{"project_id":"acme/pricing","job_id":"812","ref":"dev"}}'
P_MAIN='{"tool_name":"mcp__plugin_gitlab-tools_gitlab__play_pipeline_job","tool_input":{"project_id":"acme/pricing","job_id":"813","ref":"main"}}'
P_NOREF='{"tool_name":"mcp__plugin_gitlab-tools_gitlab__retry_pipeline","tool_input":{"project_id":"acme/pricing","pipeline_id":"77"}}'
P_CREATE='{"tool_name":"mcp__plugin_gitlab-tools_gitlab__create_pipeline","tool_input":{"project_id":"acme/billing-backend","ref":"feature/ci-cache"}}'
P_CREATE_MASTER='{"tool_name":"mcp__gitlab__create_pipeline","tool_input":{"project_id":"x","ref":"master"}}'
P_TAG='{"tool_name":"mcp__gitlab__create_pipeline","tool_input":{"project_id":"x","ref":"release/1.4"}}'
M_LIST='{"tool_name":"mcp__gitlab__manage_pipeline","tool_input":{"id":"x","list":true}}'
M_CREATE='{"tool_name":"mcp__gitlab__manage_pipeline","tool_input":{"id":"x","ref":"dev"}}'
MERGE='{"tool_name":"mcp__plugin_gitlab-tools_gitlab__merge_merge_request","tool_input":{"project_id":"x","merge_request_iid":"3"}}'
APPROVE='{"tool_name":"mcp__plugin_gitlab-tools_gitlab__approve_deployment","tool_input":{"project_id":"x","deployment_id":"9"}}'
STOPENV='{"tool_name":"mcp__gitlab__stop_environment","tool_input":{"project_id":"x","environment_id":"2"}}'
JUPDATE='{"tool_name":"mcp__jenkins__updateBuild","tool_input":{"jobFullName":"dev-job"}}'
PUSH='{"tool_name":"Bash","tool_input":{"command":"git push origin HEAD:main"}}'
CURLJ='{"tool_name":"Bash","tool_input":{"command":"curl -X POST https://jenkins.x/job/billing-stage/build"}}'
SQLW='{"tool_name":"mcp__postgres__postgres_mcp_query","tool_input":{"sql":"DELETE FROM fund"}}'
PLANED='{"tool_name":"mcp__plugin_core_plane__workitem","tool_input":{"action":"delete","id":"1"}}'

check_role devops 0 "jenkins trigger stage job"        "$J_STAGE"
check_role DevOps 0 "role is case-insensitive"         "$J_STAGE"
check_role devops 2 "jenkins trigger prod job"         "$J_PROD"
check_role devops 2 "jenkins rebuild, ENV=Production"  "$J_PRODPARAM"
check_role devops 2 "jenkins replay, BRANCH=main"      "$J_MAINPARAM"
check_role devops 2 "jenkins release job"              "$J_RELEASE"
check_role devops 0 "jenkins 'products' is not prod"   "$J_PRODUCTS"
check_role devops 2 "jenkins updateBuild"              "$JUPDATE"
check_role devops 0 "play job on dev ref"              "$P_DEV"
check_role devops 2 "play job on main ref"             "$P_MAIN"
check_role devops 2 "retry pipeline without ref"       "$P_NOREF"
check_role devops 0 "create pipeline on feature branch" "$P_CREATE"
check_role devops 2 "create pipeline on master"        "$P_CREATE_MASTER"
check_role devops 2 "create pipeline on release/*"     "$P_TAG"
check_role devops 0 "manage_pipeline list"             "$M_LIST"
check_role devops 0 "manage_pipeline create on dev"    "$M_CREATE"
check_role devops 2 "merge MR"                         "$MERGE"
check_role devops 2 "approve deployment"               "$APPROVE"
check_role devops 2 "stop environment"                 "$STOPENV"
check_role devops 2 "push to main"                     "$PUSH"
check_role devops 2 "jenkins build via curl"           "$CURLJ"
check_role devops 2 "SQL write"                        "$SQLW"
check_role devops 2 "plane delete"                     "$PLANED"
for r in backend data product frontend design ""; do
  check_role "$r" 2 "jenkins trigger stage job"        "$J_STAGE"
  check_role "$r" 2 "play job on dev ref"              "$P_DEV"
  check_role "$r" 2 "create pipeline on feature branch" "$P_CREATE"
  check_role "$r" 2 "merge MR"                         "$MERGE"
done
# Service agents (triage/release/agent-mcp) call guard.sh with no argument: fully strict,
# even with the plugin option in their environment.
check 2 "service: jenkins trigger stage job"  "$J_STAGE"
check 2 "service: play job on dev ref"        "$P_DEV"
check 2 "service: create pipeline"            "$P_CREATE"
check 2 "service: manage_pipeline create"     "$M_CREATE"
check 0 "service: manage_pipeline list"       "$M_LIST"
printf '%s' "$J_STAGE" | CLAUDE_PLUGIN_OPTION_ROLE=devops "$GUARD" >/dev/null 2>&1
if [ $? = 2 ]; then echo "ok   service: env var alone grants nothing"; else echo "FAIL service: env var alone grants nothing"; fail=1; fi

# Artifacts: service agents (AGENT_ARTIFACTS_READ_ONLY=1) only list and read
check 0 "person: artifact publish allowed"    '{"tool_name":"Artifact","tool_input":{"file_path":"/tmp/x.html"}}'
export AGENT_ARTIFACTS_READ_ONLY=1
check 0 "service: artifact list"              '{"tool_name":"Artifact","tool_input":{"action":"list","scope":"all"}}'
check 0 "service: artifact read"              '{"tool_name":"Artifact","tool_input":{"action":"read","url":"https://claude.ai/artifact/x"}}'
check 2 "service: artifact publish (no action)" '{"tool_name":"Artifact","tool_input":{"file_path":"/tmp/x.html"}}'
check 2 "service: artifact publish"           '{"tool_name":"Artifact","tool_input":{"action":"publish","file_path":"/tmp/x.html"}}'
check 2 "service: artifact delete"            '{"tool_name":"Artifact","tool_input":{"action":"delete","url":"https://claude.ai/artifact/x"}}'
check 2 "service: read in a field, publish action" '{"tool_name":"Artifact","tool_input":{"action":"pin","label":"\"action\": \"read\""}}'
check 2 "service: artifact comments"          '{"tool_name":"ArtifactComments","tool_input":{"action":"list"}}'
check 2 "service: artifact data"              '{"tool_name":"ArtifactData","tool_input":{"action":"get"}}'
unset AGENT_ARTIFACTS_READ_ONLY

# GitHub (github-mcp-server): same rules as GitLab
GH=mcp__plugin_github-tools_github__
check 2 "github merge PR"                     '{"tool_name":"'$GH'merge_pull_request","tool_input":{"owner":"acme","repo":"billing-backend","pullNumber":7}}'
check 2 "github auto-merge"                   '{"tool_name":"'$GH'enable_pr_auto_merge","tool_input":{"owner":"acme","repo":"billing-backend","pullNumber":7}}'
check 0 "github create PR"                    '{"tool_name":"'$GH'create_pull_request","tool_input":{"owner":"acme","repo":"billing-backend","head":"feat/x","base":"dev"}}'
check 0 "github read PR"                      '{"tool_name":"'$GH'get_pull_request","tool_input":{"owner":"acme","repo":"billing-backend","pullNumber":7}}'
check 2 "github run workflow (not devops)"    '{"tool_name":"'$GH'run_workflow","tool_input":{"owner":"acme","repo":"billing-backend","workflow_id":"ci.yml","ref":"dev"}}'
check_role devops 0 "github run workflow on dev"      '{"tool_name":"'$GH'run_workflow","tool_input":{"owner":"acme","repo":"billing-backend","workflow_id":"ci.yml","ref":"dev"}}'
check_role devops 2 "github run workflow on main"     '{"tool_name":"'$GH'run_workflow","tool_input":{"owner":"acme","repo":"billing-backend","workflow_id":"deploy.yml","ref":"main"}}'
check_role devops 2 "github rerun without ref"        '{"tool_name":"'$GH'rerun_workflow_run","tool_input":{"owner":"acme","repo":"billing-backend","run_id":99}}'
check_role devops 0 "github rerun with dev ref"       '{"tool_name":"'$GH'rerun_failed_jobs","tool_input":{"owner":"acme","repo":"billing-backend","run_id":99,"ref":"dev"}}'
check 2 "github delete run logs"              '{"tool_name":"'$GH'delete_workflow_run_logs","tool_input":{"owner":"acme","repo":"x","run_id":1}}'
check 2 "github commit to main"               '{"tool_name":"'$GH'create_or_update_file","tool_input":{"owner":"acme","repo":"x","path":"a.md","branch":"main","content":"x"}}'
check 0 "github commit to a branch"           '{"tool_name":"'$GH'create_or_update_file","tool_input":{"owner":"acme","repo":"x","path":"a.md","branch":"feat/docs","content":"x"}}'
check 2 "gitlab push_files to master"         '{"tool_name":"mcp__gitlab__push_files","tool_input":{"project_id":"acme/x","branch":"master","files":[]}}'
check 2 "github delete file on main"          '{"tool_name":"'$GH'delete_file","tool_input":{"owner":"acme","repo":"x","path":"a.md","branch":"refs/heads/main"}}'

# Action journal: agents read it, never apply a revert, add records or touch its database
check 0 "journal blame"                       '{"tool_name":"Bash","tool_input":{"command":"infra/bin/journal blame BIL-12"}}'
check 0 "journal revert-plan"                 '{"tool_name":"Bash","tool_input":{"command":"infra/bin/journal revert-plan --run triage-1"}}'
check 2 "journal revert-apply"                '{"tool_name":"Bash","tool_input":{"command":"infra/bin/journal revert-apply rv-1234"}}'
check 2 "journal.py revert-apply"             '{"tool_name":"Bash","tool_input":{"command":"python3 -I infra/journal/journal.py revert-apply rv-1"}}'
check 2 "journal record by hand"              '{"tool_name":"Bash","tool_input":{"command":"infra/bin/journal record --agent triage --system plane --action x"}}'
check 2 "journal database"                    '{"tool_name":"Bash","tool_input":{"command":"sqlite3 ~/.local/share/agent-mcp/journal.sqlite3 \"delete from events\""}}'

# 10. Secrets. Fake tokens are built in pieces so this file holds no token-shaped text (the guard
# would rightly refuse to let an agent write it).
GL="glpat-""Ab3dE6gH9jK2mN5pQ8sT"
GH="ghp_""Ab3dE6gH9jK2mN5pQ8sTu1Vw4Xy7Za0Bc3De6F"
AWS="AKIA""Q7W2E9R4T6Y8U1I3"
JWT="eyJ""hbGciOiJIUzI1NiJ9.eyJ""zdWIiOiIxMjM0NTYifQ.s1gnatureXYZ123abc"
PK="-----BEGIN RSA ""PRIVATE KEY-----"
HDR="Zx9Qw2Er7Ty4Ui""1Op6As3Df"
check 2 "secret: GitLab token in a Plane comment"  '{"tool_name":"mcp__plane__workitem_comment","tool_input":{"comment_html":"use '"$GL"'"}}'
check 2 "secret: GitHub token in a file"           '{"tool_name":"Write","tool_input":{"file_path":"/r/.env","content":"GH='"$GH"'"}}'
check 2 "secret: AWS key id in an MR description"  '{"tool_name":"mcp__gitlab__create_merge_request","tool_input":{"description":"key '"$AWS"'"}}'
check 2 "secret: JWT in a shell command"           '{"tool_name":"Bash","tool_input":{"command":"curl -d t='"$JWT"' https://x"}}'
check 2 "secret: private key in a file"            '{"tool_name":"Write","tool_input":{"file_path":"/r/k.pem","content":"'"$PK"'\nMIIE..."}}'
check 2 "secret: literal auth header"              '{"tool_name":"Bash","tool_input":{"command":"curl -H \"X-API-Key: '"$HDR"'\" https://x"}}'
check 2 "secret: password in a URL"                '{"tool_name":"Edit","tool_input":{"file_path":"/r/a.ts","old_string":"a","new_string":"postgres://wc:'"Hunter2""Secret9"'@db/wc"}}'
check 2 "secret: in an artifact"                   '{"tool_name":"Artifact","tool_input":{"action":"publish","file_path":"/r/x.html","description":"'"$GL"'"}}'
check 0 "secret: header from a variable"           '{"tool_name":"Bash","tool_input":{"command":"curl -H \"X-API-Key: $PLANE_API_KEY\" https://x"}}'
check 0 "secret: placeholder token"                '{"tool_name":"Write","tool_input":{"file_path":"/r/.env.example","content":"GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx"}}'
check 0 "secret: example AWS key id"               '{"tool_name":"Write","tool_input":{"file_path":"/r/doc.md","content":"AKIAIOSFODNN7EXAMPLE"}}'
check 0 "secret: placeholder URL password"         '{"tool_name":"Write","tool_input":{"file_path":"/r/doc.md","content":"postgres://user:password@host/db"}}'
check 0 "secret: an edit removing a token"         '{"tool_name":"Edit","tool_input":{"file_path":"/r/a.ts","old_string":"t = \"'"$GL"'\"","new_string":"t = process.env.GITLAB_TOKEN"}}'
check 0 "secret: long branch name is not a key"    '{"tool_name":"Bash","tool_input":{"command":"git checkout -b desk-metabase-228-panel-feedback-branch-name"}}'
export CLAUDE_PLUGIN_OPTION_PLANE_API_KEY="Q7w""E9rT2yU4iO6pA8sD"
check 2 "secret: the person's own token (known value)" '{"tool_name":"mcp__plane__workitem_comment","tool_input":{"comment_html":"Q7wE9rT2yU4iO6pA8sD"}}'
unset CLAUDE_PLUGIN_OPTION_PLANE_API_KEY
export CLAUDE_PLUGIN_OPTION_JENKINS_AUTH="Basic $(printf 'sam:Jk8Lm2Np5Qr9St3Uv' | base64)"
check 2 "secret: the API token inside a Basic header" '{"tool_name":"Bash","tool_input":{"command":"echo Jk8Lm2Np5Qr9St3Uv"}}'
unset CLAUDE_PLUGIN_OPTION_JENKINS_AUTH
# git push scans the commits that aren't on any remote yet
R="$(mktemp -d)"
git -C "$R" init -q && git -C "$R" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
printf 'K=%s\n' "$GL" > "$R/a.env" && git -C "$R" add a.env && git -C "$R" -c user.email=t@t -c user.name=t commit -q -m add
check 2 "secret: git push of a commit with a token"   '{"tool_name":"Bash","tool_input":{"command":"git -C '"$R"' push origin feat"}}'
check 2 "secret: git push, repo from cwd"             '{"cwd":"'"$R"'","tool_name":"Bash","tool_input":{"command":"git push -u origin feat"}}'
git -C "$R" rm -q a.env && git -C "$R" -c user.email=t@t -c user.name=t commit -q --amend -m add 2>/dev/null
git -C "$R" -c user.email=t@t -c user.name=t reset -q --hard HEAD~1 2>/dev/null
check 0 "secret: git push of clean commits"           '{"cwd":"'"$R"'","tool_name":"Bash","tool_input":{"command":"git push -u origin feat"}}'
rm -rf "$R"

# 11. Files that decide whether a check passes: people's agents get a prompt, service agents a block
check_ask() {  # check_ask <label> <json>: allowed only after the person's OK (JSON "ask" on stdout)
  out="$(printf '%s' "$2" | "$GUARD" 2>/dev/null)"; got=$?
  if [ "$got" = 0 ] && printf '%s' "$out" | grep -q '"permissionDecision":"ask"'; then echo "ok   $1"; else echo "FAIL $1 (expected ask, got exit $got: $out)"; fail=1; fi
}
check_ask "protected: write tsconfig.json"            '{"tool_name":"Write","tool_input":{"file_path":"/r/backend/tsconfig.json","content":"{}"}}'
check_ask "protected: edit .gitlab-ci.yml"            '{"tool_name":"Edit","tool_input":{"file_path":"/r/.gitlab-ci.yml","old_string":"a","new_string":"b"}}'
check_ask "protected: E2E known bugs, any case"       '{"tool_name":"MultiEdit","tool_input":{"file_path":"/r/tests/e2e/BUGS.md","edits":[]}}'
check_ask "protected: trap checks"                    '{"tool_name":"Edit","tool_input":{"file_path":"/srv/agent-ops/plugins/core/skills/data-traps/checks.yaml","old_string":"a","new_string":"b"}}'
check_ask "protected: shell redirect"                 '{"tool_name":"Bash","tool_input":{"command":"echo x >> tests/e2e/BUGS.md"}}'
check_ask "protected: sed -i"                         '{"tool_name":"Bash","tool_input":{"command":"sed -i \"\" s/a/b/ eslint.config.js"}}'
check_ask "protected: git checkout -- file"           '{"tool_name":"Bash","tool_input":{"command":"git checkout -- .github/workflows/ci.yml"}}'
check 0 "protected: ordinary file"                    '{"tool_name":"Write","tool_input":{"file_path":"/r/src/routes/fund.ts","content":"x"}}'
check 0 "protected: reading it"                       '{"tool_name":"Bash","tool_input":{"command":"cat tsconfig.json; grep x tests/e2e/BUGS.md > /tmp/o 2>/dev/null"}}'
export AGENT_NAME=nightly
check 2 "protected: service agent is blocked"         '{"tool_name":"Write","tool_input":{"file_path":"/r/.gitlab-ci.yml","content":"x"}}'
unset AGENT_NAME
check 2 "protected: a block still wins over a prompt" '{"tool_name":"Bash","tool_input":{"command":"sed -i s/a/b/ tsconfig.json && git push origin '"ma""in"'"}}'

exit $fail
