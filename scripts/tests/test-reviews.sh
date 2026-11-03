#!/usr/bin/env bash
# Tests for review-watcher/reviews.sh
# claude, gh and cmux are fake binaries that record argv/env. Events are written
# through events.py into a throwaway REVIEW_WATCHER_HOME.
#
# Usage: bash test-reviews.sh
# Exit 0 = all tests passed. Exit 1 = one or more failed.

set -uo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$SCRIPTS_DIR/review-watcher/reviews.sh"
EVENTS="$SCRIPTS_DIR/review-watcher/events.py"

PASS=0
FAIL=0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

assert_pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
assert_eq() { if [ "$1" = "$2" ]; then assert_pass "$3"; else assert_fail "$3 (expected '$2', got '$1')"; fi; }
assert_contains() { if printf '%s' "$1" | grep -qF -- "$2"; then assert_pass "$3"; else assert_fail "$3 ('$2' not in: $1)"; fi; }
assert_not_contains() { if printf '%s' "$1" | grep -qF -- "$2"; then assert_fail "$3 ('$2' in: $1)"; else assert_pass "$3"; fi; }

FAKE_BIN="$TMP/bin"
mkdir -p "$FAKE_BIN"

cat > "$FAKE_BIN/gh" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_LOG"
case "$*" in
  *"pr view 11"*) echo "Older PR title" ;;
  *"pr view 22"*) echo "Newer PR title" ;;
esac
FAKE

cat > "$FAKE_BIN/claude" <<'FAKE'
#!/usr/bin/env bash
{
  printf 'ARGS:'; printf ' [%s]' "$@"; echo
  echo "REVIEW_WORKER_SET=${REVIEW_WORKER+yes}"
  echo "CLAUDE_CONFIG_DIR=${CLAUDE_CONFIG_DIR:-}"
  echo "PWD=$(pwd -P)"
} > "$FAKE_CLAUDE_REC"
FAKE

# Fake cmux: records every call. FAKE_CMUX_MODE=ok|fail
cat > "$FAKE_BIN/cmux" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_CMUX_LOG"
[ "${FAKE_CMUX_MODE:-ok}" = "fail" ] && exit 1
case "$1" in
  list-workspaces) printf '  workspace:1  Main\n* workspace:7  Reviews\n' ;;
  new-workspace) echo "OK workspace:9" ;;
esac
exit 0
FAKE
chmod +x "$FAKE_BIN"/*

new_env() { # fresh state dir with two ready reviews (PR 22 requested first? no: 11 first)
  export REVIEW_WATCHER_HOME="$TMP/home-$1"
  rm -rf "$REVIEW_WATCHER_HOME"; mkdir -p "$REVIEW_WATCHER_HOME"
  export REVIEW_WATCHER_CLAUDE_BIN="$FAKE_BIN/claude"
  export REVIEW_WATCHER_GH="$FAKE_BIN/gh"
  export REVIEW_WATCHER_CMUX="$FAKE_BIN/cmux"
  export FAKE_GH_LOG="$TMP/gh-$1.log" FAKE_CLAUDE_REC="$TMP/claude-$1.rec" FAKE_CMUX_LOG="$TMP/cmux-$1.log"
  : > "$FAKE_GH_LOG"; : > "$FAKE_CMUX_LOG"
  unset CMUX_WORKSPACE_ID
  export REVIEW_WORKER=1
}

ev() { python3 "$EVENTS" append "$@" >/dev/null; }

seed_two() {
  mkdir -p "$TMP/wt-a" "$TMP/wt-b"
  # Newer request (PR 22) is logged first to prove ordering is by request ts.
  ev request_seen --repo o/r --pr 22 --request-id rid-new --ts 2026-11-02T10:00:00-08:00
  ev request_seen --repo o/r --pr 11 --request-id rid-old --ts 2026-11-02T09:00:00-08:00
  ev review_ready --repo o/r --pr 22 --request-id rid-new --ts 2026-11-02T10:30:00-08:00 \
    --data "{\"session_id\":\"sess-new\",\"cwd\":\"$TMP/wt-b\",\"run_dir\":\"x\"}"
  ev review_ready --repo o/r --pr 11 --request-id rid-old --ts 2026-11-02T09:30:00-08:00 \
    --data "{\"session_id\":\"sess-old\",\"cwd\":\"$TMP/wt-a\",\"run_dir\":\"x\"}"
}

echo "== empty queue =="
new_env empty
out=$(bash "$SCRIPT" 2>&1); rc=$?
assert_eq "$rc" "0" "empty queue exits 0"
assert_contains "$out" "No reviews ready" "empty queue message"

echo "== listing oldest first with wait times =="
new_env list
seed_two
out=$(bash "$SCRIPT" 2>&1); rc=$?
assert_eq "$rc" "0" "listing exits 0"
first=$(printf '%s\n' "$out" | grep -n 'o/r#11' | head -1 | cut -d: -f1)
second=$(printf '%s\n' "$out" | grep -n 'o/r#22' | head -1 | cut -d: -f1)
if [ -n "$first" ] && [ -n "$second" ] && [ "$first" -lt "$second" ]; then
  assert_pass "older PR listed first"
else
  assert_fail "older PR listed first (got: $out)"
fi
assert_contains "$out" "Older PR title" "title from gh"
assert_contains "$out" "waiting" "wait time shown"
assert_contains "$(grep -c 'pr view' "$FAKE_GH_LOG")" "2" "one gh call per row"

echo "== reminded counts as ready, approved does not =="
new_env states
seed_two
ev reminded --repo o/r --pr 11 --request-id rid-old
out=$(bash "$SCRIPT" 2>&1)
assert_contains "$out" "o/r#11" "reminded review listed"
ev approved --repo o/r --pr 22 --request-id rid-new
out=$(bash "$SCRIPT" 2>&1)
assert_not_contains "$out" "o/r#22" "approved review no longer listed"

echo "== select by index launches resume =="
new_env pick
seed_two
bash "$SCRIPT" 1 >/dev/null 2>&1; rc=$?
assert_eq "$rc" "0" "select exits 0"
rec=$(cat "$FAKE_CLAUDE_REC" 2>/dev/null)
assert_contains "$rec" "[--resume] [sess-old]" "resume called with stored session id"
assert_contains "$rec" "REVIEW_WORKER_SET=" "env recorded"
assert_not_contains "$rec" "REVIEW_WORKER_SET=yes" "REVIEW_WORKER not in resume env"
assert_contains "$rec" "CLAUDE_CONFIG_DIR=$HOME/.cco" "office config dir used"
assert_contains "$rec" "PWD=$(cd "$TMP/wt-a" && pwd -P)" "resume runs in saved cwd"
st=$(python3 "$EVENTS" state --json)
assert_contains "$(printf '%s' "$st" | python3 -c 'import json,sys; print([e["state"] for e in json.load(sys.stdin) if e["request_id"]=="rid-old"][0])')" "approved" "approved event appended"

echo "== select by PR number =="
new_env prnum
seed_two
bash "$SCRIPT" 22 >/dev/null 2>&1
assert_contains "$(cat "$FAKE_CLAUDE_REC")" "[sess-new]" "PR number selects that review"

echo "== open: oldest with no arg, PR number with arg =="
new_env open
seed_two
out=$(bash "$SCRIPT" open 2>&1); rc=$?
assert_eq "$rc" "0" "open exits 0"
assert_contains "$(cat "$FAKE_CLAUDE_REC")" "[sess-old]" "open with no arg resumes the oldest"
new_env openpr
seed_two
bash "$SCRIPT" open 22 >/dev/null 2>&1
assert_contains "$(cat "$FAKE_CLAUDE_REC")" "[sess-new]" "open <PR> resumes that PR"
new_env openidx
seed_two
out=$(bash "$SCRIPT" open 1 2>&1); rc=$?
assert_eq "$rc" "1" "open <n> is a PR number, never a list index"
assert_contains "$out" "no ready review for PR 1" "clear message for unknown PR"
new_env openempty
out=$(bash "$SCRIPT" open 2>&1); rc=$?
assert_eq "$rc" "1" "open on empty queue exits 1"
assert_contains "$out" "no reviews ready" "empty open message"
new_env listhint
seed_two
assert_contains "$(bash "$SCRIPT" 2>&1)" "review-open <PR number>" "listing shows review-open hint"

echo "== fallback to run dir files when event data is missing =="
new_env fallback
mkdir -p "$REVIEW_WATCHER_HOME/runs/rid-x/wt"
echo "sess-file" > "$REVIEW_WATCHER_HOME/runs/rid-x/session_id"
ev request_seen --repo o/r --pr 33 --request-id rid-x --ts 2026-11-02T09:00:00-08:00
ev review_ready --repo o/r --pr 33 --request-id rid-x
bash "$SCRIPT" 1 >/dev/null 2>&1
assert_contains "$(cat "$FAKE_CLAUDE_REC")" "[sess-file]" "session id read from run dir"

echo "== bad selection =="
new_env bad
seed_two
out=$(bash "$SCRIPT" 9 2>&1); rc=$?
assert_eq "$rc" "1" "unknown selection fails"
assert_eq "$([ -e "$FAKE_CLAUDE_REC" ] && echo launched || echo none)" "none" "no launch on bad selection"

echo "== cmux: opens the review in its own workspace =="
new_env cmux
seed_two
out=$(CMUX_WORKSPACE_ID=ws-1 bash "$SCRIPT" 1 2>&1); rc=$?
assert_eq "$rc" "0" "cmux path exits 0"
log=$(cat "$FAKE_CMUX_LOG")
assert_contains "$log" "new-workspace --cwd $TMP/wt-a --command" "new workspace created in the review cwd"
assert_contains "$log" "sess-old" "resume command sent to the new workspace"
assert_contains "$log" "env -u REVIEW_WORKER" "sent command unsets REVIEW_WORKER"
assert_contains "$log" "rename-workspace --workspace workspace:9 Review r#11" "workspace named after the PR"
assert_contains "$log" "select-workspace --workspace workspace:9" "new workspace focused"
assert_not_contains "$log" "new-surface" "no tab added to the watcher workspace"
assert_contains "$out" "Opened o/r#11 in its own workspace" "reports where it opened"
assert_contains "$(python3 "$SCRIPTS_DIR/review-watcher/reviews.py" show)" "o/r#22" "other review still listed"
assert_not_contains "$(python3 "$SCRIPTS_DIR/review-watcher/reviews.py" show)" "o/r#11" "opened review approved"
assert_eq "$([ -e "$FAKE_CLAUDE_REC" ] && echo launched || echo none)" "none" "current shell not replaced"

echo "== cmux failure falls back to current terminal =="
new_env cmuxfail
seed_two
FAKE_CMUX_MODE=fail CMUX_WORKSPACE_ID=ws-1 bash "$SCRIPT" 1 >/dev/null 2>&1
assert_contains "$(cat "$FAKE_CLAUDE_REC" 2>/dev/null)" "[sess-old]" "fell back to direct resume"

echo "== resume asks Claude to print the findings =="
new_env prompt
seed_two
bash "$SCRIPT" 1 >/dev/null 2>&1
assert_contains "$(cat "$FAKE_CLAUDE_REC" 2>/dev/null)" "Print the review findings from summary.md" "inline resume sends first prompt"
new_env prompt2
seed_two
CMUX_WORKSPACE_ID=ws-1 bash "$SCRIPT" 1 >/dev/null 2>&1
assert_contains "$(cat "$FAKE_CMUX_LOG")" "Print\\ the\\ review\\ findings" "cmux resume sends first prompt"
assert_not_contains "$(cat "$FAKE_CMUX_LOG")" "--command wt " "detached worktree: no wt"

echo "== wt worktree: workspace runs wt <branch> --no-sync, then resumes =="
new_env wtmode
mkdir -p "$TMP/wt-branch"
git -C "$TMP/wt-branch" init -q -b feat/z
ev request_seen --repo o/r --pr 33 --request-id rid-wt --ts 2026-11-02T08:00:00-08:00
ev review_ready --repo o/r --pr 33 --request-id rid-wt --ts 2026-11-02T08:30:00-08:00 \
  --data "{\"session_id\":\"sess-wt\",\"cwd\":\"$TMP/wt-branch\",\"run_dir\":\"x\"}"
out=$(CMUX_WORKSPACE_ID=ws-1 bash "$SCRIPT" open 33 2>&1); rc=$?
assert_eq "$rc" "0" "wt open exits 0"
log=$(cat "$FAKE_CMUX_LOG")
assert_contains "$log" "--command wt feat/z --no-sync; cd $TMP/wt-branch" "wt runs first, then resume in the worktree"
assert_contains "$log" "sess-wt" "resumes the review session"
assert_contains "$out" "(wt feat/z)" "reports the wt branch"

echo "== restore: an opened review goes back to the ready list =="
new_env restore
seed_two
bash "$SCRIPT" open 11 >/dev/null 2>&1
assert_not_contains "$(python3 "$SCRIPTS_DIR/review-watcher/reviews.py" show)" "o/r#11" "opened review left the list"
out=$(bash "$SCRIPT" restore 11 2>&1); rc=$?
assert_eq "$rc" "0" "restore exits 0"
assert_contains "$out" "Restored o/r#11 to the ready list." "restore reports"
row=$(python3 "$SCRIPTS_DIR/review-watcher/reviews.py" pick --pr 11)
assert_contains "$row" "sess-old" "restored review keeps its session"
assert_contains "$row" "$TMP/wt-a" "restored review keeps its worktree"
out=$(bash "$SCRIPT" restore 22 2>&1); rc=$?
assert_eq "$rc" "1" "restore of a not-opened review fails"
assert_contains "$out" "no opened review for PR 22" "restore names the problem"

echo "== rerun: retires the ready review, queues a fresh one, kicks the watcher =="
new_env rerun
seed_two
cat > "$FAKE_BIN/launchctl" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_LAUNCHCTL_LOG"
FAKE
chmod +x "$FAKE_BIN/launchctl"
export REVIEW_WATCHER_LAUNCHCTL="$FAKE_BIN/launchctl" FAKE_LAUNCHCTL_LOG="$TMP/launchctl.log"
: > "$FAKE_LAUNCHCTL_LOG"
out=$(REVIEW_WATCHER_NOW="2026-11-02T10:00:00-08:00" bash "$SCRIPT" rerun 22 2>&1); rc=$?
assert_eq "$rc" "0" "rerun exits 0"
assert_contains "$out" "Queued a fresh review of o/r#22. The watcher is starting it now." "rerun reports"
assert_not_contains "$out" "Outside office hours" "in office hours no after-hours window"
[ -e "$REVIEW_WATCHER_HOME/manual-until" ] && assert_fail "manual window opened in office hours" || assert_pass "no manual window in office hours"
assert_contains "$(cat "$FAKE_LAUNCHCTL_LOG")" "kickstart gui/$(id -u)/local.review-watcher" "watcher job kicked"
st=$(python3 "$EVENTS" state --pr 22 --json)
assert_contains "$st" "\"rid-new\"" "old request still in the log"
assert_contains "$st" "\"dismissed\"" "old ready review dismissed"
assert_contains "$st" "\"rid-new-rerun1\"" "new request queued with a rerun id"
assert_contains "$st" "\"queued\"" "new request is queued"
assert_not_contains "$(python3 "$SCRIPTS_DIR/review-watcher/reviews.py" show)" "o/r#22" "dismissed review not listed"
out=$(bash "$SCRIPT" rerun 22 2>&1); rc=$?
assert_eq "$rc" "1" "second rerun while queued refused"
assert_contains "$out" "already queued" "refusal names the state"
out=$(bash "$SCRIPT" rerun 99 2>&1); rc=$?
assert_eq "$rc" "1" "rerun of unknown PR fails"
bash "$SCRIPT" open 11 >/dev/null 2>&1
out=$(REVIEW_WATCHER_NOW="2026-11-02T20:05:00-08:00" bash "$SCRIPT" rerun 11 2>&1)
assert_contains "$out" "Outside office hours: the watcher stays on until 21:35 for this run." "after hours rerun opens the window"
assert_contains "$(cat "$REVIEW_WATCHER_HOME/manual-until")" "2026-11-02T21:35:00-08:00" "window end written"
st=$(python3 "$EVENTS" state --pr 11 --json)
assert_contains "$st" "\"approved\"" "opened review is left alone by rerun"
assert_not_contains "$st" "\"dismissed\"" "opened review not dismissed"
unset REVIEW_WATCHER_LAUNCHCTL

echo "== watcher pane: cmux failure errors, never runs inline, not approved =="
new_env pane
seed_two
out=$(FAKE_CMUX_MODE=fail CMUX_WORKSPACE_ID=ws-1 REVIEW_WATCHER_PANE=1 bash "$SCRIPT" open 2>&1); rc=$?
assert_eq "$rc" "1" "pane open without cmux fails"
assert_contains "$out" "Run review-open from another terminal" "pane failure says where to run it"
assert_eq "$([ -e "$FAKE_CLAUDE_REC" ] && echo launched || echo none)" "none" "no session in the pane"
assert_contains "$(python3 "$SCRIPTS_DIR/review-watcher/reviews.py" show)" "o/r#11" "review stays ready"

echo "== help explains states and does not launch anything =="
new_env help
seed_two
for arg in help -h --help; do
  out=$(bash "$SCRIPT" "$arg" 2>&1); rc=$?
  assert_eq "$rc" "0" "$arg exits 0"
  assert_contains "$out" "running   Claude is reviewing it now" "$arg explains running"
  assert_contains "$out" "review-open <PR>" "$arg lists review-open"
done
assert_eq "$([ -e "$FAKE_CLAUDE_REC" ] && echo launched || echo none)" "none" "help launches no session"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
