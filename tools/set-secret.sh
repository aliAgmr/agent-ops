#!/usr/bin/env bash
# Sets one secret in an env file from hidden input, so it never appears on screen, in the shell
# history or in an agent's chat. Creates the file (mode 600) if needed and replaces an existing line.
#   bash tools/set-secret.sh ~/.config/agent-ops/env TRACKER_TRIAGE_TOKEN
#   bash tools/set-secret.sh ~/.config/agent-ops/revert.env TRACKER_REVERT_TOKEN
# A person runs this in their own terminal; agents only tell them to.
set -euo pipefail
file="${1:?usage: set-secret.sh <env file> <NAME>}"; name="${2:?usage: set-secret.sh <env file> <NAME>}"
[[ "$name" =~ ^[A-Z][A-Z0-9_]*$ ]] || { echo "NAME must be UPPER_CASE" >&2; exit 2; }
[ -t 0 ] || { echo "run this in a terminal: the value is typed, hidden" >&2; exit 2; }
umask 077
mkdir -p "$(dirname "$file")"
touch "$file"; chmod 600 "$file"
IFS= read -rs -p "$name (input hidden): " value; echo
[ -n "$value" ] || { echo "empty: nothing changed" >&2; exit 1; }
tmp="$(mktemp "${file}.XXXXXX")"
grep -v "^${name}=" "$file" > "$tmp" || true
printf '%s=%s\n' "$name" "$value" >> "$tmp"
mv "$tmp" "$file"; chmod 600 "$file"
unset value
echo "$name set in $file (value not shown)"
