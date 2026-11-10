#!/usr/bin/env bash
# Keeps the read-only repo clones in AGENT_REPOS_DIR (default /srv/repos) in step with their code
# host (used by install.sh and run-agent.sh). Authenticates with the agents' read-only token for the
# clone's host: the code host in routing.yaml `code_hosts` whose hostname matches the URL
# (CODEHOST_<KEY>_TOKEN; GitLab and GitHub, through infra/bin/adapter git-auth). The header goes to
# git through GIT_CONFIG_* variables, never on a command line or in a git config file.
#
#   repo-sync.sh url <dir>               # upstream URL of a clone (follows local-path origins)
#   repo-sync.sh clone <source> <dest>   # clone from <source>'s upstream URL (or a URL)
#   repo-sync.sh set-origin <dir>...     # point origin at the upstream URL (one-time switch)
#   repo-sync.sh pull <dir>...           # fetch from the code host and fast-forward (or follow a rewrite)
#
# These clones are read-only mirrors: nobody commits in them. When the upstream branch's history
# was rewritten (a force-push or a rebase), a fast-forward is impossible and the clone would stay on
# the old history for good. `pull` then resets it to the upstream branch, but only when the clone has
# no uncommitted changes (it never discards edits); it says so on stderr.
#
# Repos named in AGENT_BLOBLESS_REPOS (space-separated folder names; default none) fetch without old
# file contents (blob:none), for large repos of which you need only a part; the checked-out files are
# still complete.
set -euo pipefail
BLOBLESS="${AGENT_BLOBLESS_REPOS-}"
ADAPTER="$(cd "$(dirname "$0")" && pwd)/adapter"

host_of() { printf '%s' "$1" | sed -nE 's#^[a-z]+://([^@/]*@)?([^/:]+).*#\2#p'; }

# Upstream URL of a clone: follow `origin` while it is a local checkout (a clone made from a
# person's checkout, whose own origin is the code host).
upstream_url() {
  local url="$1" i
  for i in 1 2 3 4 5; do
    [ -d "$url" ] || { printf '%s\n' "$url"; return; }
    url="$(git -C "$url" remote get-url origin 2>/dev/null)" || { echo "no origin in $1" >&2; return 1; }
  done
  echo "no remote origin found for $1" >&2; return 1
}

# Run git with the read-only token for <url>'s host, if there is one. Credential helpers are
# switched off so a missing or wrong token fails instead of falling back to the Keychain.
git_auth() {  # git_auth <url> <git args...>
  local url="$1"; shift
  local host hdr=""
  host="$(host_of "$url")"
  case "$url" in http*://*) hdr="$(python3 -I "$ADAPTER" git-auth "$url" 2>/dev/null || true)";; esac
  if [ -n "$hdr" ]; then
    ( export GIT_TERMINAL_PROMPT=0 GIT_CONFIG_COUNT=2 \
        GIT_CONFIG_KEY_0="http.https://$host/.extraHeader" \
        GIT_CONFIG_VALUE_0="$hdr" \
        GIT_CONFIG_KEY_1=credential.helper GIT_CONFIG_VALUE_1=
      exec git "$@" )
  else
    case "$url" in http*://*) echo "repo-sync: no read token for ${host:-$url} (CODEHOST_<KEY>_TOKEN); fetching without one" >&2;; esac
    GIT_TERMINAL_PROMPT=0 git "$@"
  fi
}

is_blobless() { case " $BLOBLESS " in *" $(basename "$1") "*) return 0;; esac; return 1; }

blobless_config() {  # partial clone settings for an existing clone
  git -C "$1" config remote.origin.promisor true
  git -C "$1" config remote.origin.partialclonefilter blob:none
}

cmd="${1:-}"; shift || true
case "$cmd" in
  url) upstream_url "${1:?usage: repo-sync.sh url <dir>}";;
  clone)
    src="${1:?usage: repo-sync.sh clone <source> <dest>}"; dest="${2:?usage: repo-sync.sh clone <source> <dest>}"
    url="$(upstream_url "$src")"
    filter=(); if is_blobless "$dest"; then filter=(--filter=blob:none); fi
    git_auth "$url" clone -q ${filter[@]+"${filter[@]}"} "$url" "$dest";;
  set-origin)
    for d in "$@"; do
      d="${d%/}"; cur="$(git -C "$d" remote get-url origin)"; url="$(upstream_url "$d")"
      if [ "$cur" != "$url" ]; then git -C "$d" remote set-url origin "$url"; echo "$(basename "$d"): origin → $url"; fi
      if is_blobless "$d"; then blobless_config "$d"; fi
    done;;
  pull)
    rc=0
    for d in "$@"; do
      d="${d%/}"; url="$(git -C "$d" remote get-url origin 2>/dev/null)" || { rc=1; continue; }
      [ -d "$url" ] && echo "repo-sync: $(basename "$d") still pulls from the local checkout $url (run: repo-sync.sh set-origin $d)" >&2
      git_auth "$url" -C "$d" fetch -q origin || { echo "repo-sync: pull failed in $d (fetch)" >&2; rc=1; continue; }
      up="$(git -C "$d" rev-parse -q --verify '@{upstream}' 2>/dev/null)" \
        || { echo "repo-sync: pull failed in $d (no upstream branch)" >&2; rc=1; continue; }
      head="$(git -C "$d" rev-parse HEAD)"
      if [ "$head" = "$up" ]; then
        :
      elif git -C "$d" merge-base --is-ancestor "$head" "$up"; then
        git -C "$d" merge -q --ff-only "$up" || { echo "repo-sync: pull failed in $d" >&2; rc=1; }
      elif [ -z "$(git -C "$d" status --porcelain --untracked-files=no)" ]; then
        branch="$(git -C "$d" rev-parse --abbrev-ref '@{upstream}')"
        old="$(git -C "$d" rev-list --count "$up..$head")"
        git -C "$d" reset -q --hard "$up" \
          && echo "repo-sync: $(basename "$d"): $branch was rewritten upstream; reset to it ($old old commits dropped)" >&2 \
          || { echo "repo-sync: pull failed in $d (reset)" >&2; rc=1; }
      else
        echo "repo-sync: pull failed in $d: diverged from upstream and has uncommitted changes (left as is)" >&2; rc=1
      fi
    done
    exit "$rc";;
  *) sed -n '2,23p' "$0" >&2; exit 2;;
esac
