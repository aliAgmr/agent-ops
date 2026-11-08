#!/usr/bin/env bash
# Runs every offline test (the same list CI runs). Needs bash, jq, git, curl, python3 ≥3.9, uv and
# node ≥18 (the n8n test skips itself without node). Exit 1 if any suite fails.
#   bash tools/run-tests.sh            all suites
#   bash tools/run-tests.sh guard journal   only suites whose name contains one of the words
set -uo pipefail
cd "$(dirname "$0")/.."
suites=(
  "guard|bash tests/guard.test.sh"
  "precheck|bash tests/precheck.test.sh"
  "run-agent|bash tests/run-agent.test.sh"
  "repo-sync|bash tests/repo-sync.test.sh"
  "post-commit-status|bash tests/post-commit-status.test.sh"
  "wake|bash tests/wake.test.sh"
  "session-notes|bash tests/session-notes.test.sh"
  "adapters|python3 tests/adapters.test.py"
  "figma-shot|python3 tests/figma-shot.test.py"
  "journal|python3 tests/journal.test.py"
  "quality|python3 tests/quality.test.py"
  "jenkins-watch|python3 tests/jenkins-watch.test.py"
  "lineage|uv run -q --no-project --with pyyaml python tests/lineage.test.py"
  "nightly|uv run -q --no-project --with pyyaml --with requests --with markdown python tests/nightly.test.py"
  "n8n|bash tests/n8n-dm.test.sh"
  "clients|uv run -q --project clients python tests/clients.test.py"
  "agent-mcp-readonly|bash tests/agent-mcp/readonly-hook.test.sh"
  "agent-mcp|bash tests/agent-mcp/run-offline.sh"
)
failed=()
for s in "${suites[@]}"; do
  name="${s%%|*}"; cmd="${s#*|}"
  if [ $# -gt 0 ]; then
    hit=""; for w in "$@"; do case "$name" in *"$w"*) hit=1;; esac; done
    [ -n "$hit" ] || continue
  fi
  [ -e "$(echo "$cmd" | grep -Eo 'tests/[^ ]+' | head -1)" ] || { echo "== $name: missing, skipped"; continue; }
  echo "== $name"
  if ! out="$(eval "$cmd" 2>&1)"; then
    echo "$out" | grep -E "FAIL|Error|error" | head -20
    failed+=("$name")
  elif echo "$out" | grep -q "^FAIL"; then
    echo "$out" | grep "^FAIL" | head -20; failed+=("$name")
  else
    echo "$out" | tail -1
  fi
done
if [ ${#failed[@]} -gt 0 ]; then echo; echo "FAILED: ${failed[*]}"; exit 1; fi
echo; echo "all suites passed"
