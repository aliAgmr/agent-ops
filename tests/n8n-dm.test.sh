#!/usr/bin/env bash
# Offline tests for the n8n plane-notify Code nodes (private messages + group topic).
#   bash tests/n8n-dm.test.sh      (needs node ≥ 18)
set -euo pipefail
command -v node >/dev/null || { echo "skip: node not installed"; exit 0; }
exec node "$(cd "$(dirname "$0")" && pwd)/n8n-dm.test.mjs"
