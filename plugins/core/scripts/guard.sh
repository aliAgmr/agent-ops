#!/usr/bin/env bash
# PreToolUse guard shared by every person's agent and both service agents.
# Blocks the team's hard rules no matter what a prompt says:
#   - no writes to any database (Postgres, ClickHouse, via MCP or CLI)
#   - no Jenkins job runs (DevOps role: non-production runs only, see below)
#   - no merges, deployments or pipeline/workflow triggers through GitLab or GitHub tools (DevOps:
#     non-production runs)
#   - no git push to main/master, by shell or by a code host's file tools
#   - no deleting Plane items
#   - the /srv/repos clones are search-only (read git subcommands, no options that run programs)
#   - service agents only list/read Claude artifacts (AGENT_ARTIFACTS_READ_ONLY=1, set by run-agent.sh)
#   - the action journal is written by its hook and scripts only: no agent applies a revert, writes
#     a record by hand or touches the journal database
#   - no secrets in anything a tool call sends (commands, files, comments, messages, git pushes)
#   - files that decide whether a check passes are edited only with a person's OK
#     (plugins/core/protected-paths.txt)
# Read-only credentials are the real guard; this hook stops the agent from even trying.
# Exit 2 = block (stderr is shown to the agent). Exit 0 = allow, or (rule 11 only) a JSON
# permissionDecision "ask" on stdout, so the person decides.
# Hooked on Bash, Write, Edit, MultiEdit, NotebookEdit, Artifact* and every MCP tool.
# Pure bash + grep + sed so it runs on every laptop (macOS, Linux, Git Bash on Windows) and on the
# agent host without extra installs.
#
# Role exemption (DevOps only): the core plugin's hook calls `guard.sh --role "<core role option>"`
# (Claude Code exports plugin options to hooks as CLAUDE_PLUGIN_OPTION_<KEY>). With role `devops`,
# Jenkins runs (trigger/rebuild/replay), GitLab pipeline runs (create/trigger/retry/play) and GitHub
# workflow runs (run/rerun) are
# allowed when nothing in the tool input names production: prod, prd, production, release, main,
# master. Everything else stays blocked for everyone. The service agents call guard.sh from their
# project settings with no argument, so they never get the exemption; the role is read from argv
# only, never from the environment.

set -u
role=""
while [ $# -gt 0 ]; do
  case "$1" in
    --role) role="${2:-}"; shift 2 || shift;;
    --role=*) role="${1#--role=}"; shift;;
    *) shift;;
  esac
done
role="$(printf '%s' "$role" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
input="$(cat | tr '\n' ' ')"

tool="$(printf '%s' "$input" | sed -n 's/.*"tool_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
[ -z "$tool" ] && exit 0
short="${tool##*__}"                       # mcp__<server>__<tool> → <tool>
lower="$(printf '%s' "$input" | sed 's/\\[nrt]/ /g' | tr '[:upper:]' '[:lower:]')"

block() {
  echo "BLOCKED by agent-ops guard: $1. This is a team hard rule (see core:scope-guard). If it is really needed, ask a maintainer (routing.yaml merge_to_main) to do it by hand." >&2
  exit 2
}

# DevOps exemption helper: the tool input (everything from "tool_input" on, so the session's cwd
# and transcript path don't count) must not name a production job, ref or environment.
tool_input="${lower#*\"tool_input\"}"
names_production() {
  printf '%s' "$tool_input" | grep -Eq '(^|[^a-z0-9])(prod|prd|production|release|releases|main|master)([^a-z0-9]|$)'
}
devops_run() {  # devops_run <what>: allow a non-production run for the devops role, else block
  [ "$role" = "devops" ] || block "$1 are not allowed for agents ($short)"
  if names_production; then
    block "$1 that touch production (prod/production/release/main/master) are done by a maintainer only ($short)"
  fi
  return 0
}

# 1. Jenkins: never run, rebuild, replay or edit a build (DevOps: non-production runs only).
case "$short" in
  triggerBuild|rebuildBuild|replayBuild)
    devops_run "Jenkins job runs";;
  updateBuild)
    block "editing Jenkins builds is not allowed for agents ($short)";;
esac

# 2. Postgres write tools.
case "$short" in
  postgres_mcp_modify|postgres_mcp_bulk_load_csv)
    block "database writes are not allowed ($short)";;
esac

# 3. GitLab: no merges, deployments, pipeline triggers or schedule/environment changes.
# DevOps may run non-production pipelines. create/trigger carry the `ref`; play/retry only carry
# ids, so the agent must name the pipeline's ref in the call (`"ref": "<branch>"`, read first with
# get_pipeline / get_pipeline_job); without a ref they stay blocked.
case "$short" in
  create_pipeline|trigger_pipeline)
    devops_run "pipeline runs";;
  play_pipeline_job|play_pipeline_jobs|retry_pipeline|retry_pipeline_job)
    devops_run "pipeline runs"
    printf '%s' "$tool_input" | grep -Eq '"ref"[[:space:]]*:[[:space:]]*"[^"]+"' ||
      block "pipeline runs by id need the pipeline's ref in the call (\"ref\": \"<branch>\", from get_pipeline/get_pipeline_job) so the guard can rule out production ($short)";;
  manage_pipeline)   # other GitLab MCP servers: one tool, list/create/retry/cancel by flags
    if printf '%s' "$tool_input" | grep -Eq '"retry"[[:space:]]*:[[:space:]]*true' ||
       ! printf '%s' "$tool_input" | grep -Eq '"(list|cancel)"[[:space:]]*:[[:space:]]*true'; then
      devops_run "pipeline runs"
      printf '%s' "$tool_input" | grep -Eq '"ref"[[:space:]]*:[[:space:]]*"[^"]+"' ||
        block "pipeline runs need the ref in the call so the guard can rule out production ($short)"
    fi;;
esac
case "$short" in
  merge_merge_request|approve_deployment|create_deployment|update_deployment|delete_deployment|\
  play_pipeline_schedule|delete_pipeline|erase_pipeline_job|\
  stop_environment|delete_environment|update_environment|stop_stale_environments|delete_review_app_environments|\
  create_pipeline_schedule|update_pipeline_schedule|delete_pipeline_schedule|take_ownership_pipeline_schedule|\
  create_pipeline_schedule_variable|update_pipeline_schedule_variable|delete_pipeline_schedule_variable|\
  create_pipeline_trigger|update_pipeline_trigger|delete_pipeline_trigger)
    block "merges, deployments and pipeline triggers are done by a maintainer only ($short)";;
esac

# 3b. GitHub (github/github-mcp-server): the same rules. Workflow runs count as pipeline runs: DevOps
# may run non-production ones, naming the ref (`run_workflow` carries it; reruns only carry ids, so
# the agent must add `"ref": "<branch>"` read from the run first). Merging and deleting run logs are
# never allowed.
case "$short" in
  run_workflow)
    devops_run "workflow runs";;
  rerun_workflow_run|rerun_failed_jobs)
    devops_run "workflow runs"
    printf '%s' "$tool_input" | grep -Eq '"ref"[[:space:]]*:[[:space:]]*"[^"]+"' ||
      block "workflow reruns by id need the run's ref in the call (\"ref\": \"<branch>\", from get_workflow_run) so the guard can rule out production ($short)";;
  merge_pull_request|enable_pr_auto_merge|delete_workflow_run_logs|delete_repository|update_branch_protection)
    block "merges, deployments and pipeline triggers are done by a maintainer only ($short)";;
esac

# 3c. Code host file tools (GitLab and GitHub): no commit straight to main/master.
case "$short" in
  push_files|create_or_update_file|delete_file|create_commit)
    if printf '%s' "$tool_input" | grep -Eq '"(branch|ref)"[[:space:]]*:[[:space:]]*"(refs/heads/)?(main|master)"'; then
      block "committing to main/master is not allowed; push a branch and open a merge/pull request ($short)"
    fi;;
esac

# 4. SQL must be read-only: checked for the known query tools and for every tool of a database or
# Metabase MCP server, so a tool renamed in a package update is still covered.
sql_tool=0
case "$short" in run_query|run_sql|postgres_mcp_query|create_card|update_card_sql) sql_tool=1;; esac
case "$tool" in mcp__*postgres*|mcp__*clickhouse*|mcp__*metabase*) sql_tool=1;; esac
# Keywords inside single-quoted string literals are data, not statements (e.g. a SELECT on
# system.query_log searching for 'create or replace table ...'), so blank literals out first.
# Escapes: '' and backslash-escaped characters (JSON doubles each backslash). If the quotes don't
# balance afterwards (an unterminated or odd literal), check the original text: never let a stray
# quote hide a real statement.
sql_text="$lower"
if [ "$sql_tool" = 1 ]; then
  stripped="$(printf '%s' "$lower" | sed -E "s/'([^'\\\\]|\\\\\\\\.|\\\\.|'')*'/''/g")"
  quotes="$(printf '%s' "$stripped" | tr -cd "'" | wc -c | tr -d ' ')"
  [ $((quotes % 2)) -eq 0 ] && sql_text="$stripped"
fi
case "$sql_tool" in
  1)
    if printf '%s' "$sql_text" | grep -Eq '(^|[^a-z_])(insert[[:space:]]+into|alter[[:space:]]+(table|database|user|role|view)|create[[:space:]]+(or[[:space:]]+replace[[:space:]]+)?(temporary[[:space:]]+)?(table|view|materialized|database|dictionary|function|user|role|index|schema)|drop[[:space:]]+(table|view|database|dictionary|user|role|index|schema|function|partition)|truncate[[:space:]]+(table[[:space:]]+)?[a-z_]|optimize[[:space:]]+table|rename[[:space:]]+table|delete[[:space:]]+from|update[[:space:]]+[a-z_."]+[[:space:]]+set|grant[[:space:]]|revoke[[:space:]]|system[[:space:]]+(stop|start|drop|reload|flush|sync|kill)|kill[[:space:]]+query)'; then
      block "the SQL contains a write/DDL statement; agents may only run SELECT queries"
    fi;;
esac

# 5. Plane: agents never delete tasks, comments or projects.
case "$tool" in
  mcp__*plane*)
    if printf '%s' "$lower" | grep -Eq '"action"[[:space:]]*:[[:space:]]*"(delete|archive|bulk_delete)'; then
      block "deleting or archiving Plane items is done by humans only"
    fi
    # Dry runs (nightly.sh --dry-run sets AGENT_TRACKER_READ_ONLY=1; AGENT_PLANE_READ_ONLY=1 is the
    # older name): the task board is read-only. The environment can only tighten the guard.
    if { [ "${AGENT_TRACKER_READ_ONLY:-}" = 1 ] || [ "${AGENT_PLANE_READ_ONLY:-}" = 1 ]; } && ! printf '%s' "$lower" \
        | grep -Eq '"action"[[:space:]]*:[[:space:]]*"(list|list_archived|retrieve|get|search|count|me)[a-z_]*"'; then
      block "this is a dry run: the task board is read-only (AGENT_TRACKER_READ_ONLY=1); record the change in the report instead"
    fi;;
esac

# 6. Shell: no direct DB clients, no Jenkins builds over HTTP, no push to main.
if [ "$tool" = "Bash" ]; then
  if printf '%s' "$lower" | grep -Eq '(^|[^a-z_-])(psql|clickhouse-client|clickhouse[[:space:]]+client|clickhouse-local)([[:space:]]|$|")'; then
    block "direct database clients are not allowed; use the read-only MCP servers"
  fi
  if printf '%s' "$lower" | grep -Eq 'jenkins[^ ]*/(job/[^ ]*/)?(build|buildwithparameters|rebuild|replay)'; then
    block "Jenkins job runs are not allowed for agents"
  fi
  if printf '%s' "$lower" | grep -Eq 'git[[:space:]]+push([^|;&]*)[[:space:]:+](main|master)([[:space:]]|$|")'; then
    block "pushing to main/master is not allowed; open a merge request to dev"
  fi
  if printf '%s' "$lower" | grep -Eq 'git[[:space:]]+push[^|;&]*[[:space:]](--force|-f)([[:space:]]|$|")'; then
    block "force-push is not allowed"
  fi
fi

# 7. The read-only repo clones in /srv/repos (triage agent): `git -C /srv/repos/<repo>` must be
# followed directly by a read subcommand (no `-c`/`--config-env`/`--exec-path` before it), and no
# option that runs a program or writes a file (`git grep -O<cmd>`, `--open-files-in-pager`,
# `--output`, `--ext-diff`, `--no-index`). Case-sensitive: `-O` is not `-o`.
if [ "$tool" = "Bash" ] && printf '%s' "$input" | grep -q '/srv/repos/'; then
  repo_git='git[[:space:]]+-C[[:space:]]+["'\'']?/srv/repos/[^[:space:]]*'
  all="$(printf '%s' "$input" | grep -Eo "$repo_git" | wc -l)"
  ok="$(printf '%s' "$input" | grep -Eo "$repo_git[[:space:]]+(grep|log|show|ls-files|ls-tree|blame|diff|status|rev-parse)([[:space:]]|\"|\$)" | wc -l)"
  if [ "$all" -ne "$ok" ]; then
    block "in /srv/repos only 'git -C /srv/repos/<repo> grep|log|show|ls-files|ls-tree|blame|diff|status|rev-parse ...' is allowed"
  fi
  if [ "$all" -gt 0 ] && printf '%s' "$input" | grep -Eq '(^|[[:space:]"])(-[a-zA-Z]*O|--open-files-in-pager|--output|--ext-diff|--no-index)'; then
    block "git options that run a program or write a file are not allowed in /srv/repos"
  fi
fi

# 8. Claude artifacts. Service agents (run-agent.sh sets AGENT_ARTIFACTS_READ_ONLY=1) may list and
# read artifacts but never publish, update, delete, pin or comment on one, or write to an artifact's
# database. A publish is the default action, so a call without "action" is blocked too. People's
# own agents are not affected. The environment can only tighten the guard, never loosen it.
if [ "${AGENT_ARTIFACTS_READ_ONLY:-}" = 1 ]; then
  case "$tool" in
    Artifact)
      printf '%s' "$tool_input" | grep -Eq '"action"[[:space:]]*:[[:space:]]*"(list|read)"' ||
        block "service agents may only list or read Claude artifacts, never publish, change or delete one";;
    ArtifactComments|ArtifactData)
      block "service agents may only list or read Claude artifacts ($tool is not allowed)";;
  esac
fi

# 9. The action journal (infra/journal): blame must be trustworthy and reverts are applied by a
# person in a terminal. Agents may read it (blame / show / suspects / revert-plan), never apply a
# plan, add records by hand, or open the database file.
if [ "$tool" = "Bash" ]; then
  if printf '%s' "$lower" | grep -Eq 'journal(\.py)?["'\'']?[[:space:]]+(revert-apply|record)([[:space:]]|$|")'; then
    block "the action journal's revert-apply and record are for people and the host's scripts, not agents; prepare the plan (revert-plan) and ask a maintainer to apply it"
  fi
  if printf '%s' "$lower" | grep -q 'journal\.sqlite3'; then
    block "the action journal's database is written only by its hook; read it with journal blame/show"
  fi
fi

# 10. Secrets never leave in a tool call: not in a command, a file, a Plane or GitLab comment, an
# MR, a message or an artifact. Two checks on what the call sends (the tool input):
#   - known values: every variable in the hook's environment whose name says it holds a secret
#     (token, key, secret, password, auth, credential) and whose value is 12+ characters and not a
#     path. Under Claude Code these include the plugins' sensitive options (CLAUDE_PLUGIN_OPTION_*),
#     so a person's own tracker, code host and CI tokens are caught whatever their format. The
#     password in a credential URL and the parts of a Basic/Bearer value are checked too.
#   - known shapes: GitLab, GitHub, Plane, Anthropic/OpenAI and Telegram tokens, AWS key ids,
#     private keys, JWTs, Slack/Mattermost webhooks, a password inside a URL, and auth headers
#     (Authorization, X-API-Key, PRIVATE-TOKEN) with a literal value instead of a $VARIABLE.
# Placeholders don't count (EXAMPLE, <token>, xxxxxxxx…: fewer than 8 distinct characters).
# `git push` also scans the lines added by the commits that aren't on any remote yet, so a secret a
# person committed by hand doesn't leave either. The message names the kind, never the value.
block_secret() {
  echo "BLOCKED by agent-ops guard: this call would send a secret ($1). Secrets never go into commands, files, commits, comments, messages or artifacts: keep them in an environment variable or a keychain setting and refer to that (e.g. \"\$TRACKER_API_KEY\"), or let the person enter it themselves. Don't try to split, encode or rephrase it to get past this check (core:scope-guard)." >&2
  exit 2
}
raw_ti="${input#*\"tool_input\"}"
placeholder() {  # placeholder <match>: true for example values and low-variety strings
  case "$1" in
    *EXAMPLE*|*example*|*Example*|*PLACEHOLDER*|*placeholder*|*REDACTED*|*redacted*|*'<'*|*'>'*|*'${'*|*'$('*) return 0;;
  esac
  [ "$(printf '%s' "$1" | fold -w1 | sort -u | wc -l | tr -d ' ')" -lt 8 ]
}
secret_shapes() {  # secret_shapes <text>: prints the kind of the first real secret found
  local text="$1" kind re m
  while IFS='|' read -r kind re; do
    [ -n "$kind" ] || continue
    while IFS= read -r m; do
      [ -n "$m" ] || continue
      # Judge only the secret part, not its prefix or header name.
      case "$kind" in
        "a private key") printf '%s' "$kind"; return 0;;
        "a password in a URL")
          m="${m#*://}"; m="${m#*:}"; m="${m%@}"
          case "$m" in password|passwd|pass|secret|changeme|'$'*|'%'*) continue;; esac;;
        "an OpenAI/Anthropic key")
          m="${m#*sk-}"
          printf '%s' "$m" | grep -q '[0-9]' && printf '%s' "$m" | grep -q '[A-Z]' || continue;;
        "a GitLab token") m="${m#*-}";;
        "a GitHub token") m="${m#*_}";;
        "a Plane token") m="${m#plane_api_}";;
        "a Telegram bot token") m="${m#*:}";;
        "a Slack webhook") m="${m##*/}";;
        "a Mattermost webhook") m="${m#*/hooks/}";;
        "an auth header value") m="${m##*[ :\"]}";;
      esac
      placeholder "$m" && continue
      printf '%s' "$kind"; return 0
    done <<EOF
$(printf '%s' "$text" | grep -Eo -- "$re" 2>/dev/null | head -n 20)
EOF
  done <<'EOF'
a GitLab token|gl(pat|dt|rt|ptt|ft|imt|oas|cbt|soat|ffct|agent)-[A-Za-z0-9_-]{20,}
a GitHub token|(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})
a Plane token|plane_api_[A-Za-z0-9]{32,}
an OpenAI/Anthropic key|(^|[^A-Za-z0-9_-])sk-(ant-[a-z0-9]+-|proj-)?[A-Za-z0-9_-]{32,}
an AWS access key id|(^|[^A-Z0-9])(AKIA|ASIA)[0-9A-Z]{16}([^A-Z0-9]|$)
a private key|-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY-----
a JWT|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}
a Telegram bot token|[0-9]{8,10}:AA[A-Za-z0-9_-]{33}
a Slack webhook|hooks\.slack\.com/services/[A-Za-z0-9]+/[A-Za-z0-9]+/[A-Za-z0-9]{20,}
a Mattermost webhook|/hooks/[a-z0-9]{26}([^a-z0-9]|$)
a password in a URL|[A-Za-z][A-Za-z0-9+.-]*://[^/[:space:]:@"\\]+:[^/[:space:]@"\\]{6,}@
an auth header value|([Aa]uthorization|X-API-Key|X-Api-Key|x-api-key|PRIVATE-TOKEN|Private-Token)\\?"?:[[:space:]]*\\?"?([Bb]asic |[Bb]earer |[Tt]oken )?[A-Za-z0-9+/._=-]{20,}
EOF
  return 1
}
secret_values() {  # secret_values <text>: prints which variable's value the text contains
  local text="$1" name val part dec
  while IFS= read -r name; do
    case "$name" in ''|SSH_AUTH_SOCK|*_PATH|*_FILE|*_DIR|*_URL_PATH) continue;; esac
    val="${!name-}"
    [ "${#val}" -ge 12 ] || continue
    case "$val" in /*|'~'*|./*) continue;; esac
    for part in "$val" "${val#* }"; do
      case "$part" in *://*@*) part="${part#*://}"; part="${part#*:}"; part="${part%%@*}";; *://*) continue;; esac
      [ "${#part}" -ge 12 ] || continue
      case "$text" in *"$part"*) printf 'the value of $%s' "$name"; return 0;; esac
    done
    case "$val" in
      [Bb]asic\ *)
        dec="$(printf '%s' "${val#* }" | base64 -d 2>/dev/null || printf '%s' "${val#* }" | base64 -D 2>/dev/null)"
        part="${dec#*:}"
        if [ "${#part}" -ge 12 ] && [ "$part" != "$dec" ]; then
          case "$text" in *"$part"*) printf 'the API token inside $%s' "$name"; return 0;; esac
        fi;;
    esac
  done <<EOF
$(compgen -e | grep -iE 'token|key|secret|passw|auth|credential')
EOF
  return 1
}
# What the call sends: for an edit, only the new text (removing a secret must stay possible).
scan_ti="$raw_ti"
case "$tool" in
  Edit|MultiEdit) scan_ti="$(printf '%s' "$raw_ti" | sed -E 's/"old_string"[[:space:]]*:[[:space:]]*"([^"\\]|\\.)*"//g')";;
esac
if kind="$(secret_values "$scan_ti")" || kind="$(secret_shapes "$scan_ti")"; then
  block_secret "$kind"
fi
if [ "$tool" = "Bash" ] && printf '%s' "$lower" | grep -Eq '(^|[^a-z_-])git([[:space:]]+-c[[:space:]]+[^[:space:]]+)*[[:space:]]+push([[:space:]]|$|")'; then
  repo_dir="$(printf '%s' "$raw_ti" | sed -n 's/.*git[[:space:]]\{1,\}-C[[:space:]]\{1,\}[\\"'\'']*\([^\\"'\'' ]*\).*/\1/p' | head -n1)"
  [ -n "$repo_dir" ] || repo_dir="$(printf '%s' "$input" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)"
  if [ -n "$repo_dir" ] && git -C "$repo_dir" rev-parse --git-dir >/dev/null 2>&1; then
    added="$(git -C "$repo_dir" log -p -n 50 --no-color --no-ext-diff --format= HEAD --not --remotes 2>/dev/null \
      | grep '^+' | grep -v '^+++' | head -c 2000000)"
    if [ -n "$added" ]; then
      if kind="$(secret_values "$added")" || kind="$(secret_shapes "$added")"; then
        block_secret "$kind, in a commit that isn't pushed yet; remove it from the commit (and rotate it if it was ever shared)"
      fi
    fi
  fi
fi

# 11. Files that decide whether a check passes (CI, lint/type/test config, the E2E known-bugs list,
# agent-ops' own checks, hooks and Claude settings; list: plugins/core/protected-paths.txt).
# Agents tend to make a red check green by loosening the check instead of fixing the code. An edit
# to one of them (Write/Edit/MultiEdit/NotebookEdit, or a shell command that writes, copies, moves
# or deletes it) needs a person: people's agents get a permission prompt (the person approves or
# not), service agents are blocked (nobody to ask; run-agent.sh sets AGENT_NAME). Matching ignores
# case (macOS file names do). The shell check is a heuristic: a write verb plus a protected name
# anywhere in the command. Placed last so that a block above always wins over a prompt.
protected_list="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)/protected-paths.txt"
protected_patterns=""
[ -f "$protected_list" ] && protected_patterns="$(grep -v '^[[:space:]]*#' "$protected_list" | grep -v '^[[:space:]]*$' | tr '[:upper:]' '[:lower:]')"
is_protected() {  # is_protected <lower-case path>
  local p="${1#./}" pat
  while IFS= read -r pat; do
    [ -n "$pat" ] || continue
    # shellcheck disable=SC2254
    case "$p" in $pat|*/$pat) return 0;; esac
  done <<EOF
$protected_patterns
EOF
  return 1
}
protected_hit=""
if [ -n "$protected_patterns" ]; then
  case "$tool" in
    Write|Edit|MultiEdit|NotebookEdit)
      while IFS= read -r f; do
        [ -n "$f" ] && is_protected "$f" && { protected_hit="$f"; break; }
      done <<EOF
$(printf '%s' "$tool_input" | grep -Eo '"(file_path|notebook_path)"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*:[[:space:]]*"\(.*\)"/\1/')
EOF
      ;;
    Bash)
      # Redirections to /dev/null and between descriptors are not writes. A redirection writes only
      # its target; a write command (tee, cp, mv, rm, sed -i, …) may write any name in the command.
      cmd="$(printf '%s' "$tool_input" | sed -E 's/[0-9]*>&[0-9-]//g; s/[0-9&]*>>?[[:space:]]*\/dev\/null//g')"
      targets="$(printf '%s' "$cmd" | grep -Eo '>>?[[:space:]]*[\\"'"'"']*[^[:space:];|&)"\\'"'"'}]+' | sed -E 's/^>>?[[:space:]]*//' | tr -d '"\\'"'")"
      if printf '%s' "$cmd" | grep -Eq '(^|[^a-z_-])(tee|cp|mv|rm|truncate|install|ln|chmod|rsync|unlink)[[:space:]]|sed[[:space:]]+(-[a-z]+[[:space:]]+)*-[a-z]*i|perl[[:space:]]+-[a-z]*i|git[[:space:]]+(checkout|restore)[[:space:]]'; then
        targets="$targets
$(printf '%s' "$cmd" | tr -cs 'a-z0-9._/~+@*-' '\n')"
      fi
      while IFS= read -r w; do
        [ -n "$w" ] && is_protected "$w" && { protected_hit="$w"; break; }
      done <<EOF
$targets
EOF
      ;;
  esac
fi
if [ -n "$protected_hit" ]; then
  why="it decides whether a check passes (plugins/core/protected-paths.txt). Fix the code instead of loosening the check, unless the task is about this file"
  if [ -n "${AGENT_NAME:-}" ]; then
    block "service agents never edit $protected_hit: $why"
  fi
  safe="$(printf '%s' "$protected_hit" | tr -d '"\\')"
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"agent-ops guard: %s needs your OK. %s."}}\n' "$safe" "$why"
  exit 0
fi

exit 0
