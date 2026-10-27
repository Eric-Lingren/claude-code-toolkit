#!/bin/bash
# sync-worktree-base.sh — point a fresh agent worktree at the shared branch tip.
#
# Usage: sync-worktree-base.sh <base-sha> [worktree-path]
#
# build-code spawns build-runner with isolation="worktree". The harness may
# create that worktree from a different commit than the feature branch
# (e.g. main). Without a sync, the runner is missing the feature work and
# improvises cherry-picks, which auto mode blocks.
#
# Safety: only moves HEAD when the worktree is a linked worktree, has a clean
# tree, and holds no commits of its own (nothing outside <base> and main).
# Uses `git reset --keep`, which refuses to drop local changes.
#
# Exit codes: 0 synced or already at base, 1 usage/env error, 3 refused.
set -euo pipefail

base="${1:-}"
wt="${2:-$PWD}"
[ -n "$base" ] || { echo "usage: $0 <base-sha> [worktree-path]" >&2; exit 1; }

g() { git -C "$wt" "$@"; }

g rev-parse --git-dir >/dev/null 2>&1 || { echo "not a git repo: $wt" >&2; exit 1; }
base_sha=$(g rev-parse --verify --quiet "$base^{commit}") || { echo "unknown base: $base" >&2; exit 1; }

git_dir=$(cd "$wt" && cd "$(git rev-parse --git-dir)" && pwd -P)
common_dir=$(cd "$wt" && cd "$(git rev-parse --git-common-dir)" && pwd -P)
[ "$git_dir" != "$common_dir" ] || { echo "refused: $wt is the main checkout, not a linked worktree" >&2; exit 3; }

head_sha=$(g rev-parse HEAD)
if [ "$head_sha" = "$base_sha" ]; then
  echo "already at base ${base_sha:0:10}"
  exit 0
fi

if g merge-base --is-ancestor "$base_sha" HEAD; then
  echo "HEAD already contains base ${base_sha:0:10}; nothing to do"
  exit 0
fi

[ -z "$(g status --porcelain)" ] || { echo "refused: worktree has uncommitted changes" >&2; exit 3; }

# Exclude every trunk ref: a stale local main must not hide newer origin/main commits.
excludes=()
for ref in main master origin/main origin/master; do
  g rev-parse --verify --quiet "$ref" >/dev/null && excludes+=("^$ref")
done
own=$(g rev-list HEAD "^$base_sha" ${excludes[@]+"${excludes[@]}"} | wc -l | tr -d ' ')
[ "$own" = "0" ] || { echo "refused: worktree has $own commit(s) not in base or trunk" >&2; exit 3; }

g reset --keep "$base_sha" >/dev/null
echo "synced ${head_sha:0:10} -> ${base_sha:0:10}"
