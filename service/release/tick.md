Run one release tick.

1. For each repo in routing.yaml (`teams` and every `projects.*.teams`, on any code host), except
   repos marked `release: false`, list open merge requests (GitLab) or pull requests (GitHub) with
   target branch `main`, through that host's MCP server (`gitlab` or `github`).
2. For each MR whose head commit has no `release-agent` commit status yet (or whose previous
   status was posted for an older head commit), follow the `release-check` skill.
   Skip an MR whose CI pipeline for the head commit is still running; it will be checked next tick.
3. For MRs into `main` merged since the last successful run (time given at the end of this
   prompt): move every linked board item that is in `Ready for E2E` with label `e2e:green` to
   `Done`, and comment `[agent:release] in production via <MR link>`.
4. Finish with a short summary: MRs/PRs checked (repo!iid or repo#number → green/red, reason), tasks moved to Done.
   If there was nothing to do, reply "nothing to do". If the code host or the board couldn't be read, start
   the summary with `ERROR: <what failed>` instead (see "Unattended-run rules" in CLAUDE.md).
