Run one Jenkins watcher tick.

1. Read `~/.local/share/agent-ops/jenkins-watch/facts.json` (written by `collect` minutes ago). If its
   `checked_at` is more than 20 minutes old or the file is missing, reply
   `ERROR: facts.json missing or stale (collect failed)` and stop.
2. For each item in `events` (`new`, `again`, `recovered`):
   - `failed` (new/again): read the end of the failed build's log (`getBuildLog` with a negative
     `limit` for the last lines, or `searchBuildLog` for `error|exception|failed|timeout|refused`), and
     check the job now (`getJob`: is a newer build running or green?). Decide the verdict:
     `attention` (a real error a person must fix: a code or SQL error, a missing table, a permission,
     a data problem, or the same failure repeating) or `transient` (see "Don't cry wolf" in
     CLAUDE.md). For a job of one of the team's projects, look at recent commits in its repo when the
     error points at code or SQL.
   - `stuck`: the build has run `running_min` minutes against a usual `usual_min`; check its log tail
     for progress (still printing? waiting on a lock or a query?). Verdict `stuck`, or `transient` if it
     finished meanwhile.
   - `queue`: builds waiting to start for `since_min` minutes; `getStatus` for executors. Verdict `stuck`.
   - `recovered`: verdict `recovered`, one line (e.g. "green again since build #1234").
3. `alerts`: new messages from other bots in the topic. Each lists the `jobs` it mentions. Every alert
   about a job you assess gets a reply (its `message_id` in that item's `reply_to`). An alert about a job
   with no event (an older failure already handled, or one `collect` doesn't treat as open) still gets a
   short reply from what you can see in Jenkins now (`post: false` if it needn't be in the summary).
4. Write `~/.local/share/agent-ops/jenkins-watch/decisions.json` (the Write tool):
   ```json
   {"headline": "<optional one line, e.g. '2 need attention, 1 blip'>",
    "items": [{"job": "<full job name>", "verdict": "attention|stuck|transient|recovered",
               "project": "<projects.yaml key, infra or other>", "owner": "<people.yaml key or empty>",
               "line": "<one line for the summary>", "post": true,
               "reply_to": [<alert message_ids and, for recovered/again, the incident's earlier `messages`>],
               "reply": "<one or two sentences to reply with, or empty>"}]}
   ```
   `post: false` keeps a job out of the summary (e.g. a blip you only reply to). An empty `items` list
   means nothing to say.
5. Run `python3 /srv/agent-ops/infra/jenkins-watch/watch.py post` (one plain command). It prints the
   message ids it sent.
6. Finish with one line: `<n> attention, <n> stuck, <n> blips, <n> recovered; <n> replies` (or
   `nothing to post`), or `ERROR: <what failed>` when Jenkins or the post couldn't be read/sent.
