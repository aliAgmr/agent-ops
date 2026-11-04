# Adapters: the task board and the code host

Everything in agent-ops that talks to the task board (tracker) or the code host goes through these
two small interfaces, so supporting another tool is one file, not a rewrite. Python 3.9+ standard
library only (the host scripts and agent-mcp import them; shell scripts call `infra/bin/adapter`).

| Kind | Built in | Config | Add yours |
|---|---|---|---|
| Tracker (task board) | `plane` | `routing.yaml` → `tracker: plane`; env `TRACKER_BASE_URL`, `TRACKER_WORKSPACE`, tokens `TRACKER_*_TOKEN` | `tracker/<name>.py` implementing `TrackerAdapter` |
| Code host | `gitlab`, `github` (github.com and Enterprise) | `routing.yaml` → `code_hosts: {key: {type, url}}`; tokens `CODEHOST_<KEY>_*` | `codehost/<name>.py` implementing `CodeHostAdapter` |

The agents themselves talk to the tracker and code host through MCP servers (Plane MCP, GitLab MCP,
GitHub MCP); the adapters cover what scripts and agent-mcp do without a model: prechecks, identity,
snapshots for the action journal, commit statuses, repo clones, nightly facts.

## TrackerAdapter (`tracker/base.py`)

Items are plain dicts with at least: `id`, `key` (e.g. `BIL-12`), `project_id`, `name`, `state`
(id), `state_name`, `state_group` (`backlog|unstarted|started|completed|cancelled`), `labels`
(names), `label_ids`, `assignees` (ids), `priority`, `parent`, `description_html`, `created_at`,
`updated_at`. Times are ISO 8601 UTC.

| Method | Returns | Used by |
|---|---|---|
| `whoami()` | `{id, email, display_name}` for the adapter's token | agent-mcp auth, journal |
| `projects()` | `[{id, identifier, name, is_member}]` | agent-mcp auth, precheck |
| `states(project_id)` / `labels(project_id)` / `members(project_id)` | `[{id, name, group?}]` / `[{id, name}]` / `[{id, email, display_name}]` | precheck, configure, journal revert |
| `list_items(project_id, *, state_names=None, label_names=None, updated_since=None)` | items | precheck, nightly, session notes |
| `get_item(project_id, item_id)` / `get_item_by_key(key)` | item | journal snapshots, quality, blame |
| `update_item(project_id, item_id, fields)` | item | journal revert |
| `list_comments(project_id, item_id)` / `add_comment(...)` / `update_comment(...)` / `delete_comment(...)` | | precheck, journal revert |
| `delete_link(project_id, item_id, link_id)` | | journal revert |
| `activities(project_id, item_id)` | `[{actor, field, old_value, new_value, created_at}]` | blame (by-hand changes) |

## CodeHostAdapter (`codehost/base.py`)

"Merge request" means a GitLab MR or a GitHub pull request; `iid` is the MR iid or PR number;
`repo` is the `path` in routing.yaml (`group/name` or `owner/name`).

| Method | Returns | Used by |
|---|---|---|
| `whoami()` | `{username}` | setup checks |
| `list_merge_requests(repo, *, state="opened", target_branch=None, updated_after=None)` | `[{iid, title, source_branch, target_branch, sha, web_url, author, updated_at, merged_at}]` | precheck (release), nightly |
| `get_merge_request(repo, iid)` / `merge_request_files(repo, iid)` | MR dict / `[path]` | release, journal snapshots |
| `commits(repo, ref, since)` | `[{sha, title, message, author_name, created_at}]` | nightly |
| `statuses(repo, sha, name=None)` / `post_status(repo, sha, state, name, description, target_url=None)` | | release status (`state`: `pending|success|failed|canceled`; GitHub maps failed→failure, canceled→error) |
| `update_merge_request(repo, iid, fields)` / `close_merge_request(repo, iid)` / `delete_note(repo, kind, iid, note_id)` | | journal revert |
| `clone_url(repo, with_token=False)` | https URL (token only in memory, never printed) | repo-sync |

## CLI

`infra/bin/adapter tracker <method> [json-args]` and `infra/bin/adapter codehost <key> <method>
[json-args]` print JSON; tokens come from the env file, never from arguments. Exit 2 on an
unknown method, 1 on an API error (message on stderr, token redacted).

## Adding a tracker (e.g. Jira, Linear, GitHub Issues)
1. `tracker/<name>.py`: a class implementing `TrackerAdapter`; map your states onto the five
   `state_group`s and your issue keys onto `key`.
2. Register it in `infra/adapters/__init__.py` (`TRACKERS`).
3. Point the agents' MCP config at an MCP server for that tool (`service/*/.mcp.json`, core plugin).
4. `tests/adapters.test.py`: add a fake-API test like the Plane one.
