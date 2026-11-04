#!/bin/bash
# commit-change-records.sh — on-demand entry point: write change records and commit them.
# What: runs write-change-records.py --commit, which writes a record for every merge on main that
#   has none, then makes ONE local commit on the current branch holding only the new
#   attribution/change-records/ files. No commit when nothing is new. Unrelated working-tree changes are
#   never included. Then pushes to origin/main, but only when those record commits are the only
#   unpushed work on local main (see attribution/change_records_push.py: fetch, rebase onto GitHub's
#   main, push; pull.ff=only rules out a plain pull). Otherwise the push is skipped with a
#   "warning:" line. A failed push also only warns; the exit code stays 0. Refuses (exit 0, message) unless the checked-out branch is main:
#   the writer scans main, so records committed on a feature branch, detached HEAD or mid-rebase
#   would land off main and later conflict.
# When: by hand any time, and from scripts/usage/weekly-usage-report.sh (weekly, after merges).
#
# Usage: commit-change-records.sh [extra write-change-records.py args]
# Env:   CC_CHANGE_RECORDS_REPO  repo to scan and commit in (default ~/.dotfiles)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# launchd starts with a bare PATH; gh lives in /opt/homebrew/bin. Without it every CI snapshot
# lookup fails and the null is written permanently.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
REPO="${CC_CHANGE_RECORDS_REPO:-$HOME/.dotfiles}"

BRANCH="$(git -C "$REPO" symbolic-ref --short -q HEAD 2>/dev/null || true)"
if [ "$BRANCH" != "main" ]; then
  echo "skipped: $REPO is on '${BRANCH:-detached HEAD}', not main; change records are only committed on main"
  exit 0
fi

# CC_CHANGE_RECORDS_REPO redirects the records folder along with the repo; otherwise the
# writer's default (the shared helper's folder) applies.
OUT_ARGS=()
[ -n "${CC_CHANGE_RECORDS_REPO:-}" ] && OUT_ARGS=(--out-dir "$REPO/attribution/change-records")

exec /usr/bin/python3 "$HERE/write-change-records.py" --repo "$REPO" "${OUT_ARGS[@]+"${OUT_ARGS[@]}"}" --commit "$@"
