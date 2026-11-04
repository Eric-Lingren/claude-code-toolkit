#!/usr/bin/env bash
# change-revert.sh - revert one merged change from its change record.
#
#   change-revert.sh <change_id>   revert one merged change and open a revert PR (alias: change-revert)
#
# Looks up attribution/change-records/<change_id>.json, runs `git revert` on its merged_sha
# (with -m 1 when that commit is a merge commit, since hand-made PRs land as merge commits),
# pushes the result on a new branch revert/<change_id> and opens a normal PR with
# `gh pr create`. The PR goes through the same gate as any other PR. It never merges.
#
# The revert is built in a temporary git worktree off the base branch, so the checkout you
# run it from is never touched: not its branch, index or files, and it need not be clean.
# An unknown change_id, a record whose commit is not on the base branch, or a revert
# conflict exits non-zero with a message; the revert is aborted and the temporary worktree
# and branch are removed, so nothing is left behind and nothing is pushed.
#
# Test hooks / overrides (all optional):
#   CHANGE_REVERT_REPO          repo to operate on (default: the repo this script lives in)
#   CHANGE_REVERT_CHANGES_DIR   where change records live (default: the folder printed by scripts/shared/change_records_path.py)
#   CHANGE_REVERT_BASE_BRANCH   branch the revert is based on and the PR targets (default: main)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${CHANGE_REVERT_REPO:-$(cd "$SCRIPT_DIR" && git rev-parse --show-toplevel 2>/dev/null)}"
BASE_BRANCH="${CHANGE_REVERT_BASE_BRANCH:-main}"

usage() { echo "usage: change-revert.sh <change_id>" >&2; }
die() { echo "change-revert: $*" >&2; exit 1; }

[ $# -ge 1 ] || { usage; exit 2; }
case "$1" in
  -h|--help|help) usage; exit 0 ;;
  revert) usage; exit 2 ;;
esac
[ $# -eq 1 ] || { usage; exit 2; }

CHANGE_ID="$1"
[ -n "$REPO" ] && [ -d "$REPO" ] || die "cannot find the repo (set CHANGE_REVERT_REPO)"
git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $REPO"

if ! printf '%s' "$CHANGE_ID" | grep -qE '^chg-[0-9a-f]{12}$'; then
  usage
  die "'$CHANGE_ID' is not a change id (expected chg-<12 hex characters>)"
fi

CHANGES_DIR="${CHANGE_REVERT_CHANGES_DIR:-$(python3 "$SCRIPT_DIR/../shared/change_records_path.py")}"
RECORD="$CHANGES_DIR/$CHANGE_ID.json"
[ -f "$RECORD" ] || die "no change record for $CHANGE_ID (looked in $CHANGES_DIR)"

MERGED_SHA=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["merged_sha"])' "$RECORD" 2>/dev/null) \
  || die "could not read merged_sha from $RECORD"
printf '%s' "$MERGED_SHA" | grep -qE '^[0-9a-f]{40}$' || die "record $CHANGE_ID has no valid merged_sha"

# Base on the freshest main we can see: origin's when there is one, else the local branch.
if git -C "$REPO" remote get-url origin >/dev/null 2>&1; then
  git -C "$REPO" fetch --quiet origin "$BASE_BRANCH" 2>/dev/null || true
fi
BASE_REF="$BASE_BRANCH"
git -C "$REPO" rev-parse --verify --quiet "refs/remotes/origin/$BASE_BRANCH" >/dev/null && BASE_REF="origin/$BASE_BRANCH"
git -C "$REPO" rev-parse --verify --quiet "$BASE_REF^{commit}" >/dev/null || die "base branch '$BASE_BRANCH' not found"

git -C "$REPO" cat-file -e "$MERGED_SHA^{commit}" 2>/dev/null || die "commit $MERGED_SHA for $CHANGE_ID is not in this repo"
git -C "$REPO" merge-base --is-ancestor "$MERGED_SHA" "$BASE_REF" \
  || die "commit $MERGED_SHA for $CHANGE_ID is not on $BASE_REF, nothing to revert"

BRANCH="revert/$CHANGE_ID"
git -C "$REPO" rev-parse --verify --quiet "refs/heads/$BRANCH" >/dev/null && die "branch $BRANCH already exists"

SUBJECT=$(git -C "$REPO" log -1 --format=%s "$MERGED_SHA")
PARENTS=$(git -C "$REPO" rev-list --parents -n1 "$MERGED_SHA" | wc -w | tr -d ' ')
REVERT_ARGS=(--no-edit)
[ "$PARENTS" -gt 2 ] && REVERT_ARGS=(-m 1 --no-edit)

HOLD=$(mktemp -d "${TMPDIR:-/tmp}/change-revert.XXXXXX") || die "could not create a temp dir"
WT="$HOLD/wt"
cleanup() {
  git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1
  rmdir "$HOLD" 2>/dev/null
  git -C "$REPO" worktree prune >/dev/null 2>&1
}
fail_clean() { # message: drop the temp worktree and the local branch, then exit 1
  local msg="$1"
  git -C "$WT" revert --abort >/dev/null 2>&1
  cleanup
  git -C "$REPO" branch -D "$BRANCH" >/dev/null 2>&1
  die "$msg"
}

git -C "$REPO" worktree add --quiet -b "$BRANCH" "$WT" "$BASE_REF" >/dev/null 2>&1 \
  || { cleanup; git -C "$REPO" branch -D "$BRANCH" >/dev/null 2>&1; die "could not create a temporary worktree for $BRANCH"; }

if ! git -C "$WT" revert "${REVERT_ARGS[@]}" "$MERGED_SHA" >/dev/null 2>&1; then
  fail_clean "revert conflict: $MERGED_SHA ($SUBJECT) does not revert cleanly on $BASE_REF. Revert aborted, nothing changed. Revert it by hand."
fi

git -C "$WT" push --quiet -u origin "$BRANCH" >/dev/null 2>&1 \
  || fail_clean "could not push $BRANCH to origin"

BODY="Reverts \`$MERGED_SHA\` ($SUBJECT), change record \`$CHANGE_ID\`.

Opened by \`change-revert $CHANGE_ID\`. This PR goes through the normal gate and is not merged automatically."
PR_URL=$(cd "$WT" && gh pr create --base "$BASE_BRANCH" --head "$BRANCH" \
  --title "Revert: $SUBJECT" --body "$BODY") \
  || { cleanup; die "$BRANCH was pushed but gh pr create failed. Open the PR by hand."; }

cleanup
echo "Reverted $CHANGE_ID ($MERGED_SHA) on branch $BRANCH."
echo "$PR_URL"
