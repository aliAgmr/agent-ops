#!/usr/bin/env bash
# Thin wrapper: the precheck itself is infra/bin/precheck.py (task board and code host through
# infra/adapters). Kept so run-agent.sh and the docs call one stable path.
exec python3 -I "$(cd "$(dirname "$0")" && pwd)/precheck.py" "$@"
