#!/usr/bin/env bash
# Thin wrapper: see infra/bin/post-commit-status.py (GitLab and GitHub through infra/adapters).
# Usage: post-commit-status.sh <repo-path> <sha> <success|failed|pending> "<description>" [target-url]
exec python3 -I "$(cd "$(dirname "$0")" && pwd)/post-commit-status.py" "$@"
