#!/usr/bin/env bash
# Tests for review-watcher/run-review.sh
# claude and gh are replaced by fake binaries. The fake claude writes the review
# outputs (or tries to post, or reports permission denials) depending on
# FAKE_CLAUDE_MODE. Nothing real is called and nothing reaches GitHub.
#
# Usage: bash test-run-review.sh
# Exit 0 = all tests passed. Exit 1 = one or more failed.

set -uo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$SCRIPTS_DIR/review-watcher/run-review.sh"
POST="$SCRIPTS_DIR/egress/post-pr-review.py"

PASS=0
FAIL=0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

assert_pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
assert_eq() { if [ "$1" = "$2" ]; then assert_pass "$3"; else assert_fail "$3 (expected '$2', got '$1')"; fi; }
assert_contains() { if printf '%s' "$1" | grep -qF -- "$2"; then assert_pass "$3"; else assert_fail "$3 ('$2' not in: $1)"; fi; }
assert_file() { if [ -s "$1" ]; then assert_pass "$2"; else assert_fail "$2 (missing/empty: $1)"; fi; }

FAKE_BIN="$TMP/bin"
mkdir -p "$FAKE_BIN"

# Fake gh: records every call; never touches the network.
cat > "$FAKE_BIN/gh" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_LOG"
exit 0
FAKE

# Fake claude: records argv and env, then acts per FAKE_CLAUDE_MODE.
cat > "$FAKE_BIN/claude" <<'FAKE'
#!/usr/bin/env bash
{
  printf 'ARGS:'; printf ' [%s]' "$@"; echo
  echo "REVIEW_WORKER=${REVIEW_WORKER:-}"
  echo "CLAUDE_CONFIG_DIR=${CLAUDE_CONFIG_DIR:-}"
} > "$FAKE_CLAUDE_REC"
RUN_DIR="$FAKE_RUN_DIR"
write_outputs() {
  echo '[{"file":"a.py","line":3,"body":"nit"}]' > "$RUN_DIR/findings.json"
  echo '# Summary' > "$RUN_DIR/summary.md"
}
case "${FAKE_CLAUDE_MODE:-ok}" in
  ok)
    write_outputs
    echo '{"type":"result","session_id":"sess-123","permission_denials":[]}' ;;
  try-post)
    # Worker tries to post. Must be refused by the REVIEW_WORKER guard.
    write_outputs
    python3 "$FAKE_POST" --pr 7 --repo o/r --findings "$RUN_DIR/findings.json" 2> "$RUN_DIR/post-attempt.err"
    echo "post-exit=$?" > "$RUN_DIR/post-attempt.rc"
    echo '{"type":"result","session_id":"sess-post","permission_denials":[]}' ;;
  denied)
    echo '{"type":"result","session_id":"sess-den","permission_denials":[{"tool_name":"Bash","tool_input":{"command":"nc example.test 80"}},{"tool_name":"WebFetch","tool_input":{"url":"https://x.test"}}]}' ;;
  crash)
    echo "boom" >&2; exit 3 ;;
esac
FAKE
chmod +x "$FAKE_BIN/gh" "$FAKE_BIN/claude"
# Stand-in alerts.py: records each call, so no real banner fires from a test.
cat > "$FAKE_BIN/alerts.py" <<'FAKE'
import os, sys
with open(os.environ["FAKE_ALERTS_LOG"], "a") as fh:
    fh.write(" ".join(sys.argv[1:]) + "\n")
FAKE
export REVIEW_WATCHER_ALERTS="$FAKE_BIN/alerts.py" FAKE_ALERTS_LOG="$TMP/alerts.log"

run_case() { # NAME MODE -> sets RC, OUT, RUN_DIR, HOME_DIR, REC, GHLOG
  local name="$1" mode="$2"
  RUN_DIR="$TMP/$name/run"; HOME_DIR="$TMP/$name/home"
  REC="$TMP/$name/claude.rec"; GHLOG="$TMP/$name/gh.log"
  mkdir -p "$RUN_DIR" "$HOME_DIR"
  : > "$GHLOG"; : > "$FAKE_ALERTS_LOG"
  OUT=$(PATH="$FAKE_BIN:$PATH" REVIEW_WATCHER_HOME="$HOME_DIR" \
    REVIEW_WATCHER_CLAUDE_BIN="$FAKE_BIN/claude" FAKE_CLAUDE_MODE="$mode" \
    FAKE_CLAUDE_REC="$REC" FAKE_RUN_DIR="$RUN_DIR" FAKE_GH_LOG="$GHLOG" FAKE_POST="$POST" \
    bash "$SCRIPT" --repo o/r --pr 7 --request-id req-1 --run-dir "$RUN_DIR" 2>&1)
  RC=$?
}
event_types() {
  REVIEW_WATCHER_HOME="$HOME_DIR" python3 - <<'PY'
import json, os
p = os.path.join(os.environ["REVIEW_WATCHER_HOME"], "events.jsonl")
print(" ".join(json.loads(l)["type"] for l in open(p) if l.strip()))
PY
}
gh_calls() { wc -l < "$GHLOG" | tr -d ' '; }

echo "== success path (stubbed end to end)"
run_case ok ok
assert_eq "$RC" "0" "exits 0"
assert_file "$RUN_DIR/findings.json" "findings.json written"
assert_file "$RUN_DIR/summary.md" "summary.md written"
assert_eq "$(cat "$RUN_DIR/session_id" 2>/dev/null)" "sess-123" "session_id captured"
assert_eq "$(event_types)" "review_started review_ready" "log: review_started then review_ready"
READY_LINE=$(grep '"review_ready"' "$HOME_DIR/events.jsonl")
assert_contains "$READY_LINE" "\"run_dir\":\"$RUN_DIR\"" "review_ready records run_dir"
assert_contains "$READY_LINE" "\"session_id\":\"sess-123\"" "review_ready records session_id"
assert_contains "$READY_LINE" "\"cwd\":\"$(pwd)\"" "review_ready records launch cwd"
REC_TXT=$(cat "$REC")
assert_contains "$REC_TXT" "REVIEW_WORKER=1" "REVIEW_WORKER=1 exported"
assert_contains "$REC_TXT" "CLAUDE_CONFIG_DIR=$HOME/.cco" "CLAUDE_CONFIG_DIR exported from config"
assert_contains "$REC_TXT" "[-p]" "headless -p"
assert_contains "$REC_TXT" "[--allowedTools]" "allowlist passed"
assert_contains "$REC_TXT" "[Bash(gh pr view:*)]" "allowlist has gh pr view"
assert_contains "$REC_TXT" "[Agent]" "allowlist has Agent"
assert_contains "$REC_TXT" "--headless $RUN_DIR" "prompt asks for headless mode in run dir"
ALLOWED_SEG=$(printf '%s' "$REC_TXT" | sed -n 's/.*\[--allowedTools\]\(.*\)\[--disallowedTools\].*/\1/p')
assert_contains "$ALLOWED_SEG" "[Read]" "allowedTools segment parsed"
case "$ALLOWED_SEG" in
  *"gh pr review"*|*"gh pr comment"*|*"gh api"*|*"git push"*) assert_fail "allowlist contains a write verb" ;;
  *) assert_pass "allowedTools segment is read-only" ;;
esac
assert_contains "$REC_TXT" "[Bash(gh pr review:*)]" "write verbs are in the deny list"
assert_eq "$(gh_calls)" "0" "no gh call from runner"
assert_eq "$(cat "$FAKE_ALERTS_LOG")" "run" "alerts step runs once the review is ready"

echo "== post attempt from inside the worker is refused"
run_case post try-post
assert_eq "$RC" "0" "run still completes"
assert_contains "$(cat "$RUN_DIR/post-attempt.rc")" "post-exit=1" "post exits non-zero"
assert_contains "$(cat "$RUN_DIR/post-attempt.err")" "REVIEW_WORKER" "refusal names REVIEW_WORKER"
assert_eq "$(gh_calls)" "0" "gh never called (nothing on the PR)"

echo "== denied tool calls are logged"
run_case den denied
assert_eq "$RC" "1" "run fails when outputs are missing"
EV=$(event_types)
assert_contains "$EV" "tool_denied" "tool_denied event in log"
assert_contains "$EV" "failed" "failed event in log"
case "$EV" in *review_ready*) assert_fail "must not append review_ready" ;; *) assert_pass "no review_ready on failure" ;; esac
LOGTXT=$(cat "$HOME_DIR/events.jsonl")
assert_contains "$LOGTXT" "Bash(nc example.test 80)" "denied Bash command named"
assert_contains "$LOGTXT" "WebFetch" "denied WebFetch named"
assert_contains "$OUT" "denied tools" "failure message points at denied tools"
assert_eq "$(gh_calls)" "0" "no gh call on failure"

echo "== claude crash"
run_case crash crash
assert_eq "$RC" "1" "exits 1"
assert_eq "$(cat "$FAKE_ALERTS_LOG")" "run" "alerts step runs after a failure"
assert_contains "$(event_types)" "failed" "failed event appended"

echo "== missing binary"
HOME_DIR="$TMP/nobin/home"; mkdir -p "$HOME_DIR"
OUT=$(REVIEW_WATCHER_HOME="$HOME_DIR" REVIEW_WATCHER_CLAUDE_BIN="$TMP/does-not-exist" \
  bash "$SCRIPT" --repo o/r --pr 7 --request-id req-1 --run-dir "$TMP/nobin/run" 2>&1); RC=$?
assert_eq "$RC" "1" "exits 1 when claude binary missing"
assert_contains "$(event_types)" "failed" "failed event appended"

echo "== alerts step failing does not change the result"
printf 'import sys\nsys.exit(5)\n' > "$TMP/bad-alerts.py"
REVIEW_WATCHER_ALERTS="$TMP/bad-alerts.py" run_case badalerts ok
assert_eq "$RC" "0" "still exits 0 when alerts.py fails"
REVIEW_WATCHER_ALERTS="" run_case noalerts ok
assert_eq "$RC" "0" "empty REVIEW_WATCHER_ALERTS skips the step"
assert_eq "$(cat "$FAKE_ALERTS_LOG")" "" "skipped step records nothing"

echo "== usage"
OUT=$(bash "$SCRIPT" --repo o/r 2>&1); RC=$?
assert_eq "$RC" "2" "missing args exits 2"

echo "== config"
assert_eq "$(python3 "$SCRIPTS_DIR/review-watcher/config.py" show CLAUDE_BIN)" "\"$HOME/.local/bin/claude\"" "CLAUDE_BIN default is the real launcher"

echo
echo "Passed: $PASS  Failed: $FAIL"
[ "$FAIL" -eq 0 ]
