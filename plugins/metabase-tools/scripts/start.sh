#!/usr/bin/env bash
# Starts the vendored Metabase MCP server. Installs its two npm deps once into a data dir
# that survives plugin updates (CLAUDE_PLUGIN_DATA), or a cache dir when run by a service agent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${CLAUDE_PLUGIN_DATA:-$HOME/.cache/agent-ops/metabase-tools}"
mkdir -p "$DATA"
if ! cmp -s "$HERE/server/package-lock.json" "$DATA/package-lock.json" 2>/dev/null; then
  cp "$HERE/server/package.json" "$HERE/server/package-lock.json" "$DATA/"
  npm ci --prefix "$DATA" --omit=dev --silent >&2
fi
export NODE_PATH="$DATA/node_modules"
cp "$HERE/server/index.mjs" "$DATA/index.mjs"   # ESM resolves imports next to the file
exec node "$DATA/index.mjs"
