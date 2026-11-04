#!/usr/bin/env bash
# Offline tests for the agent-mcp server (fake Plane, fake claude). Run: bash tests/agent-mcp/run-offline.sh
set -euo pipefail
exec uv run --quiet --script "$(cd "$(dirname "$0")" && pwd)/test_offline.py"
