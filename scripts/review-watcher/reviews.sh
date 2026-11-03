#!/usr/bin/env bash
# reviews.sh - list ready PR reviews and resume one.
#
#   reviews.sh            list ready reviews, oldest first (PR, title, wait)
#   reviews.sh open       open the oldest ready review           (alias: review-open)
#   reviews.sh open <PR>  open the ready review for that PR number (alias: review-open <PR>)
#   reviews.sh help       what each queue state means, what to do next (alias: review-help)
#   reviews.sh restore <PR>  put an opened review back in the ready list (alias: review-restore)
#   reviews.sh rerun <PR>    review the PR again from scratch (alias: review-rerun)
#   reviews.sh <n|PR>     legacy: list index (1 = oldest) or PR number
#
# "Open" means the review's Claude session in the terminal, not GitHub. You read
# the findings there and tell Claude to post (or not).
#
# Selecting appends an `approved` event, then resumes that review's saved claude
# session (`claude --resume <session_id>`) from the directory it ran in, without
# REVIEW_WORKER so posting is possible. Posting still needs approval inside the
# session. The resume sends a first message asking Claude to print summary.md as
# plain text (the Write preview of it renders too faint to read).
#
# Inside cmux it opens a new workspace, so the review never runs in the Review
# Watcher pane. When the review ran in a `wt` worktree (cwd on a branch, not
# detached), the workspace first runs `wt <branch> --no-sync`, which installs deps
# and makes the usual split panes, then resumes Claude in the main pane. A detached
# run worktree just resumes, in a workspace named "Review <repo>#<PR>". Outside cmux, or if that fails, it
# runs in the current terminal, except under REVIEW_WATCHER_PANE=1 (set by the
# queue view), where it errors instead. The approval is recorded only once the
# session is opened.
#
# Test hooks: REVIEW_WATCHER_HOME, REVIEW_WATCHER_CLAUDE_BIN, REVIEW_WATCHER_GH,
# REVIEW_WATCHER_CMUX (cmux binary, default `cmux`).
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1
CMUX="${REVIEW_WATCHER_CMUX:-cmux}"

if [ $# -eq 0 ]; then
  exec python3 reviews.py show
fi
case "$1" in
  help|-h|--help) exec python3 reviews.py help ;;
  restore|rerun)
    [ $# -eq 2 ] || { echo "usage: reviews.sh $1 <PR number>" >&2; exit 2; }
    exec python3 reviews.py "$1" "$2" ;;
esac

jget() { python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

if [ "$1" = "open" ]; then
  if [ $# -ge 2 ]; then
    ROW=$(python3 reviews.py pick --pr "$2") || { echo "review-open: no ready review for PR $2 (run: reviews)" >&2; exit 1; }
  else
    ROW=$(python3 reviews.py pick 1) || { echo "review-open: no reviews ready" >&2; exit 1; }
  fi
else
  ROW=$(python3 reviews.py pick "$1") || { echo "reviews.sh: no ready review matches '$1'" >&2; exit 1; }
fi
REPO=$(printf '%s' "$ROW" | jget repo)
PR=$(printf '%s' "$ROW" | jget pr)
RID=$(printf '%s' "$ROW" | jget request_id)
SESSION=$(printf '%s' "$ROW" | jget session_id)
WCWD=$(printf '%s' "$ROW" | jget cwd)

if [ -z "$SESSION" ]; then
  echo "reviews.sh: no saved session for $REPO#$PR (request $RID)" >&2
  exit 1
fi
if [ ! -d "$WCWD" ]; then
  echo "reviews.sh: saved working directory is gone: $WCWD" >&2
  exit 1
fi

CFG=$(python3 reviews.py config)
BIN=$(printf '%s' "$CFG" | jget claude_bin)
CONFIG_DIR=$(printf '%s' "$CFG" | jget config_dir)

record_approval() {
  python3 events.py append approved --repo "$REPO" --pr "$PR" --request-id "$RID" >/dev/null \
    || { echo "reviews.sh: could not record approval" >&2; exit 1; }
}

FIRST_PROMPT="Print the review findings from summary.md in full as plain text, then ask whether to post them to the PR."
RESUME=$(printf 'cd %q && env -u REVIEW_WORKER CLAUDE_CONFIG_DIR=%q %q --resume %q %q' \
  "$WCWD" "$CONFIG_DIR" "$BIN" "$SESSION" "$FIRST_PROMPT")
# A `wt` worktree is on a branch; a detached run worktree has none.
BRANCH=$(git -C "$WCWD" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
WS_COMMAND="$RESUME"
# `;` not `&&`: if wt fails, still resume (RESUME cds back to the worktree itself).
[ -n "$BRANCH" ] && WS_COMMAND=$(printf 'wt %q --no-sync; %s' "$BRANCH" "$RESUME")

open_in_cmux() {
  [ -n "${CMUX_WORKSPACE_ID:-}" ] && command -v "$CMUX" >/dev/null 2>&1 || return 1
  local out ws
  out=$("$CMUX" new-workspace --cwd "$WCWD" --command "$WS_COMMAND" 2>/dev/null) || return 1
  ws=$(printf '%s' "$out" | grep -oE 'workspace:[0-9]+' | head -1)
  # Created but no ref to rename: still opened, so do not fall back. In wt mode
  # _gx_open_worktree renames it again to the branch.
  [ -n "$ws" ] || return 0
  "$CMUX" rename-workspace --workspace "$ws" "Review ${REPO##*/}#$PR" >/dev/null 2>&1 || true
  "$CMUX" select-workspace --workspace "$ws" >/dev/null 2>&1 || true
}

if open_in_cmux; then
  record_approval
  if [ -n "$BRANCH" ]; then
    echo "Opened $REPO#$PR in its own workspace (wt $BRANCH)."
  else
    echo "Opened $REPO#$PR in its own workspace (Review ${REPO##*/}#$PR)."
  fi
  exit 0
fi

if [ -n "${REVIEW_WATCHER_PANE:-}" ]; then
  echo "review-open: could not open a cmux workspace for $REPO#$PR. Run review-open from another terminal." >&2
  exit 1
fi

record_approval

cd "$WCWD" || exit 1
exec env -u REVIEW_WORKER CLAUDE_CONFIG_DIR="$CONFIG_DIR" "$BIN" --resume "$SESSION" "$FIRST_PROMPT"
