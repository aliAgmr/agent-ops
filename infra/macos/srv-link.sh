#!/usr/bin/env bash
# Gives a Mac agent host the same /srv paths as a Linux one, so prompts, permission rules and scripts
# in this repo are identical on both hosts. macOS's root volume is read-only, so /srv is a
# "synthetic" link (/etc/synthetic.conf) to a folder in the agent user's home.
# Run once with sudo; no reboot needed. Idempotent.
#
#   sudo bash infra/macos/srv-link.sh /Users/<agent-user>/agent-host
set -euo pipefail
TARGET="${1:?usage: sudo srv-link.sh <absolute target dir, e.g. /Users/me/agent-host>}"
[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }
case "$TARGET" in /*) ;; *) echo "target must be absolute" >&2; exit 1;; esac

entry="srv	${TARGET#/}"   # name<TAB>target relative to /
if [ -L /srv ]; then
  echo "/srv already links to $(readlink /srv)"
  exit 0
fi
[ -e /srv ] && { echo "/srv exists and is not a link; not touching it" >&2; exit 1; }

touch /etc/synthetic.conf
grep -q "^srv	" /etc/synthetic.conf || printf '%s\n' "$entry" >> /etc/synthetic.conf
/System/Library/Filesystems/apfs.fs/Contents/Resources/apfs.util -t || true
[ -L /srv ] && echo "/srv → $(readlink /srv)" || echo "/srv appears after the next reboot"
