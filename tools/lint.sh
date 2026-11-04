#!/usr/bin/env bash
# Repository lint (CI job `lint`): JSON parses, shell scripts parse and are executable, every
# SKILL.md has name + description front matter, and the leak check is clean.
set -euo pipefail
cd "$(dirname "$0")/.."
rc=0
for f in $(git ls-files -co --exclude-standard '*.json'); do jq empty "$f" >/dev/null 2>&1 || { echo "invalid JSON: $f"; rc=1; }; done
for f in $(git ls-files -co --exclude-standard '*.sh'); do
  bash -n "$f" || { echo "bad shell: $f"; rc=1; }
  test -x "$f" || { echo "not executable: $f"; rc=1; }
done
for f in $(git ls-files -co --exclude-standard '*/SKILL.md'); do
  head -1 "$f" | grep -q '^---$' && grep -q '^name:' "$f" && grep -q '^description:' "$f" \
    || { echo "bad skill front matter: $f"; rc=1; }
done
python3 tools/leak-check.py >/dev/null || { python3 tools/leak-check.py | tail -20; rc=1; }
[ "$rc" = 0 ] && echo "lint: clean"
exit "$rc"
