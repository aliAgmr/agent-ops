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
     CLAUDE.md).
   - **Every `attention` failure is investigated in its repo** (`repo`, and `code` when set; about 15
     tool calls per job) before you write its line:
     1. The job's definition: find it with `git -C /srv/repos/<repo> ls-files` (its Jenkinsfile or
        job config, e.g. `jenkins/jobs/<job>.xml`), then `Read` it: which step, script or SQL file
        the failing log line belongs to.
     2. That file: `Read` the part that failed (the query, the table, the command).
     3. What changed: `git -C /srv/repos/<repo> log --since=14.days --format="%h %an %ad %s" --date=short -- <files>`,
        then `git -C /srv/repos/<repo> show <sha> -- <file>` for a commit that touches what failed.
     4. Say what you found in the line and the reply: the file (and line) that fails, the likely
        cause, and the commit (short sha, author, date) when one matches; or "no recent change to
        <file>; likely data or environment" when none does. Facts you saw, not guesses; a guess says so.
     A job with no definition file in a repo (defined only in Jenkins): find the script in the log's
     command lines and search `code` with `git grep`. A job with neither `repo` nor `code`, and a
     transient blip, need no repo work.
   - `stuck`: the build has run `running_min` minutes against a usual `usual_min`; check its log tail
     for progress (still printing? waiting on a lock or a query?). Verdict `stuck`, or `transient` if it
     finished meanwhile.
   - `queue`: builds waiting to start for `since_min` minutes; `getStatus` for executors. Verdict `stuck`.
   - `recovered`: verdict `recovered` with a one-sentence `reply` (e.g. "Green again since build
     #1234."). `post` sends it as one reply under the first post about it (never a summary line,
     never a mention), whatever you put in `post` and `reply_to`.
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
