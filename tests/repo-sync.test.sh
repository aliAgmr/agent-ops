#!/usr/bin/env bash
# Tests for infra/bin/repo-sync.sh with real git, offline: local "code host" repos stand in for the
# hosted projects (file:// URLs), and a checkout of each stands in for a person's local checkout.
# Token handling against https URLs is covered in tests/run-agent.test.sh (fake git).
# Run: bash tests/repo-sync.test.sh
set -u
SYNC="$(cd "$(dirname "$0")/.." && pwd)/infra/bin/repo-sync.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t \
  GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
unset CODEHOST_MAIN_TOKEN CODEHOST_OSS_TOKEN
export AGENT_OPS_ENV=/dev/null AGENT_BLOBLESS_REPOS=warehouse   # the big shared repo fetches blobless
fail=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi; }
commit() { echo "$2" > "$1/f.txt"; git -C "$1" add f.txt; git -C "$1" commit -q -m "$2"; }

mkdir -p "$T/gitlab" "$T/dev" "$T/srv"
for r in billing-backend warehouse; do
  git init -q -b dev "$T/gitlab/$r"; commit "$T/gitlab/$r" one
  git -C "$T/gitlab/$r" config uploadpack.allowFilter true
  git clone -q "file://$T/gitlab/$r" "$T/dev/$r"       # a person's checkout: origin = the code host
  git clone -q "$T/dev/$r" "$T/srv/$r"                  # old /srv/repos clone: origin = checkout
done
B="$T/srv/billing-backend"; D="$T/srv/warehouse"

# 1. url follows a local-path origin to the the code host URL
check "url follows the checkout's origin" '[ "$(bash "$SYNC" url "$B")" = "file://$T/gitlab/billing-backend" ]'

# 2. the old setup only sees what the checkout pulled; after set-origin, pull sees the code host
commit "$T/gitlab/billing-backend" two
bash "$SYNC" pull "$B" 2>"$T/err"
check "old clone: pull misses the code host's new commit" '[ "$(cat "$B/f.txt")" = one ]'
check "old clone: pull warns about the local origin" 'grep -q "still pulls from the local checkout" "$T/err"'
out="$(bash "$SYNC" set-origin "$T/srv/"*/)"
check "set-origin points origin at the code host" '[ "$(git -C "$B" remote get-url origin)" = "file://$T/gitlab/billing-backend" ] && echo "$out" | grep -q "billing-backend: origin → file://"'
check "set-origin again changes nothing" '[ -z "$(bash "$SYNC" set-origin "$T/srv/"*/)" ]'
bash "$SYNC" pull "$T/srv/"*/ 2>"$T/err"; rc=$?
check "switched clone: pull fast-forwards to the code host" '[ "$rc" = 0 ] && [ "$(cat "$B/f.txt")" = two ] && [ ! -s "$T/err" ]'

# 3. warehouse: blobless fetches, files still checked out
check "warehouse is set up blobless" '[ "$(git -C "$D" config remote.origin.partialclonefilter)" = blob:none ] && [ "$(git -C "$D" config remote.origin.promisor)" = true ]'
check "other repos stay full clones" '[ -z "$(git -C "$B" config remote.origin.partialclonefilter)" ]'
commit "$T/gitlab/warehouse" two
bash "$SYNC" pull "$D" 2>/dev/null
check "blobless pull fast-forwards" '[ "$(cat "$D/f.txt")" = two ]'
check "blobless pull fetched a promisor pack" 'ls "$D/.git/objects/pack/"*.promisor >/dev/null 2>&1'

# 4. clone: from a checkout → origin is the code host; warehouse cloned blobless
rm -rf "$B" "$D"
bash "$SYNC" clone "$T/dev/billing-backend" "$B"
bash "$SYNC" clone "$T/dev/warehouse" "$D"
check "clone from a checkout gets the the code host origin" '[ "$(git -C "$B" remote get-url origin)" = "file://$T/gitlab/billing-backend" ] && [ "$(cat "$B/f.txt")" = two ]'
check "warehouse clone is blobless" '[ "$(git -C "$D" config remote.origin.partialclonefilter)" = blob:none ] && [ "$(cat "$D/f.txt")" = two ]'

# 5. a failing pull is reported (exit 1) but the other repos are still pulled
rm -rf "$T/gitlab/billing-backend"; commit "$T/gitlab/warehouse" three
bash "$SYNC" pull "$B" "$D" 2>"$T/err"; rc=$?
check "failed pull: exit 1, the rest still pulled" '[ "$rc" = 1 ] && grep -q "pull failed in $B" "$T/err" && [ "$(cat "$D/f.txt")" = three ]'

# 6. upstream history rewritten (force-push of dev): a clean clone follows it, a clone with edits doesn't
G="$T/gitlab/warehouse"
git -C "$G" checkout -q --orphan new && commit "$G" rewritten && git -C "$G" branch -q -M new dev
bash "$SYNC" pull "$D" 2>"$T/err"; rc=$?
check "rewritten upstream: a clean clone is reset to it" '[ "$rc" = 0 ] && [ "$(cat "$D/f.txt")" = rewritten ] && grep -q "was rewritten upstream" "$T/err"'
check "rewritten upstream: the clone is exactly the upstream branch" '[ "$(git -C "$D" rev-parse HEAD)" = "$(git -C "$G" rev-parse dev)" ]'
commit "$G" rewritten-again
git -C "$G" checkout -q --orphan new2 && commit "$G" third-history && git -C "$G" branch -q -M new2 dev
echo "local edit" > "$D/f.txt"
bash "$SYNC" pull "$D" 2>"$T/err"; rc=$?
check "rewritten upstream with local edits: left as is, reported" '[ "$rc" = 1 ] && [ "$(cat "$D/f.txt")" = "local edit" ] && grep -q "uncommitted changes" "$T/err"'
git -C "$D" checkout -q -- f.txt
bash "$SYNC" pull "$D" 2>/dev/null; rc=$?
check "after the edit is gone, the next pull follows the rewrite" '[ "$rc" = 0 ] && [ "$(cat "$D/f.txt")" = third-history ]'

exit "$fail"
