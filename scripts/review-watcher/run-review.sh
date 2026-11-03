#!/usr/bin/env bash
# Headless review worker: runs /pr-code-review for one PR and stops before posting.
#
# Usage: run-review.sh --repo ORG/REPO --pr N --run-dir DIR [--request-id ID]
#
# Appends review_started, launches `claude -p` headless with REVIEW_WORKER=1 and a
# read-only tool allowlist, then appends review_ready (findings.json, summary.md and
# session_id all present) or failed. Every permission denial reported by claude is
# appended as a tool_denied event, so a failed run names the tool to add to the
# allowlist. Nothing here posts to GitHub; post-pr-review.py also refuses under
# REVIEW_WORKER=1. Binary, config dir and timeout come from config.py.
#
# The claude session runs in the cwd run-review.sh was launched from (the watcher
# passes a detached worktree of the PR). review_ready records run_dir, session_id
# and cwd so /reviews can resume the session from that cwd.
#
# Either way it then runs the alerts step (alerts.py run), so the ready or failure
# banner fires as soon as the run ends, not on the next watcher tick.
# REVIEW_WATCHER_ALERTS swaps alerts.py; set but empty skips the step (tests).
#
# Exit 0 = review ready. Exit 1 = failed. Exit 2 = bad usage.

set -uo pipefail

LAUNCH_CWD="$(pwd)"  # the review runs here (the watcher passes the PR worktree)
HERE="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$HERE/config.py"
EVENTS="$HERE/events.py"
ALERTS="${REVIEW_WATCHER_ALERTS-$HERE/alerts.py}"

REPO="" PR="" RUN_DIR="" REQUEST_ID=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --pr) PR="$2"; shift 2 ;;
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --request-id) REQUEST_ID="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
if [ -z "$REPO" ] || [ -z "$PR" ] || [ -z "$RUN_DIR" ]; then
  echo "usage: run-review.sh --repo ORG/REPO --pr N --run-dir DIR [--request-id ID]" >&2
  exit 2
fi

mkdir -p "$RUN_DIR"
RUN_DIR="$(cd "$RUN_DIR" && pwd)"
cfg() { python3 "$CONFIG" show "$1" | python3 -c 'import json,sys;print(json.load(sys.stdin))'; }
CLAUDE_BIN="$(cfg CLAUDE_BIN)"
CLAUDE_CONFIG_DIR_VAL="$(cfg CLAUDE_CONFIG_DIR)"
NODE_BIN_DIR="$(cfg NODE_BIN_DIR)"
# launchd has no fnm setup: without node on PATH every node-based hook fails.
[ -d "$NODE_BIN_DIR" ] && export PATH="$NODE_BIN_DIR:$PATH"
TIMEOUT_MIN="$(cfg REVIEW_TIMEOUT_MINUTES)"

RID_ARGS=()
[ -n "$REQUEST_ID" ] && RID_ARGS=(--request-id "$REQUEST_ID")

event() { # TYPE [extra args...]
  local t="$1"; shift
  python3 "$EVENTS" append "$t" --repo "$REPO" --pr "$PR" ${RID_ARGS[@]+"${RID_ARGS[@]}"} "$@" >/dev/null
}

# Banners now. A dead cmux socket or missing alerter must not change the exit code.
run_alerts() {
  [ -n "$ALERTS" ] && python3 "$ALERTS" run >/dev/null 2>&1 || true
}

fail() {
  echo "run-review: $1" >&2
  event failed --reason "$1"
  run_alerts
  exit 1
}

event review_started

if [ ! -x "$CLAUDE_BIN" ]; then
  fail "claude binary not executable: $CLAUDE_BIN"
fi

# Read-only allowlist. Write is scoped to the run directory (// = absolute path).
ALLOWED=(
  "Read" "Grep" "Glob" "Agent"
  "Bash(gh pr view:*)" "Bash(gh pr diff:*)" "Bash(gh pr list:*)"
  "Bash(gh pr checks:*)" "Bash(gh repo view:*)" "Bash(gh issue view:*)"
  "Bash(git diff:*)" "Bash(git log:*)" "Bash(git show:*)" "Bash(git fetch:*)"
  "Bash(git status:*)" "Bash(git branch:*)" "Bash(git merge-base:*)"
  "Bash(git rev-parse:*)" "Bash(git ls-files:*)" "Bash(git blame:*)"
  "Write(/${RUN_DIR}/**)"
)
# Belt and braces: deny wins over allow, so write verbs stay blocked.
DENIED=(
  "Bash(gh pr review:*)" "Bash(gh pr comment:*)" "Bash(gh pr merge:*)"
  "Bash(gh pr edit:*)" "Bash(gh pr close:*)" "Bash(gh issue comment:*)"
  "Bash(gh api:*)" "Bash(git push:*)" "Bash(*post-pr-review*)"
)

PROMPT="/pr-code-review $PR --headless $RUN_DIR
Repo: $REPO. Review PR #$PR on that repo (use gh pr diff $PR --repo $REPO). Write findings.json and summary.md in $RUN_DIR, then stop. Never post anything to GitHub."

RESULT="$RUN_DIR/claude-result.json"
STDERR="$RUN_DIR/claude-stderr.log"
# Clear outputs of any earlier attempt so a stale file cannot pass the checks.
python3 - "$RUN_DIR" <<'PY'
import os, sys
for n in ("findings.json", "summary.md", "session_id", "claude-result.json"):
    try:
        os.unlink(os.path.join(sys.argv[1], n))
    except FileNotFoundError:
        pass
PY

TIMEOUT_CMD=()
if command -v timeout >/dev/null 2>&1; then TIMEOUT_CMD=(timeout "${TIMEOUT_MIN}m")
elif command -v gtimeout >/dev/null 2>&1; then TIMEOUT_CMD=(gtimeout "${TIMEOUT_MIN}m"); fi

(
  cd "$LAUNCH_CWD" &&
  REVIEW_WORKER=1 CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR_VAL" \
    ${TIMEOUT_CMD[@]+"${TIMEOUT_CMD[@]}"} "$CLAUDE_BIN" -p "$PROMPT" \
    --output-format json \
    --allowedTools "${ALLOWED[@]}" \
    --disallowedTools "${DENIED[@]}" \
    --add-dir "$RUN_DIR"
) >"$RESULT" 2>"$STDERR"
RC=$?

# Session id (line 1) and denied tools (one per line after), whatever the exit code.
PARSED="$(python3 - "$RESULT" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    d = {}
if not isinstance(d, dict):
    d = {}
print(d.get("session_id") or "")
for x in d.get("permission_denials") or []:
    name = x.get("tool_name") or x.get("tool") or "unknown"
    detail = x.get("tool_input") or {}
    cmd = detail.get("command") if isinstance(detail, dict) else None
    print(name + (f"({cmd})" if cmd else ""))
PY
)"
SESSION_ID="$(printf '%s\n' "$PARSED" | head -n1)"
DENIALS="$(printf '%s\n' "$PARSED" | tail -n +2)"

DENIED_NAMES=""
if [ -n "$DENIALS" ]; then
  while IFS= read -r tool; do
    [ -z "$tool" ] && continue
    event tool_denied --tool "$tool"
    DENIED_NAMES="${DENIED_NAMES:+$DENIED_NAMES, }$tool"
  done <<<"$DENIALS"
fi

[ -n "$SESSION_ID" ] && printf '%s\n' "$SESSION_ID" >"$RUN_DIR/session_id"

HINT=""
[ -n "$DENIED_NAMES" ] && HINT=" (denied tools, add to allowlist if needed: $DENIED_NAMES)"

[ "$RC" -ne 0 ] && fail "claude exited $RC$HINT"
[ -z "$SESSION_ID" ] && fail "no session_id in claude output$HINT"
[ -s "$RUN_DIR/summary.md" ] || fail "summary.md missing$HINT"
python3 - "$RUN_DIR/findings.json" <<'PY' || fail "findings.json missing or invalid$HINT"
import json, sys
d = json.load(open(sys.argv[1]))
assert isinstance(d, list) and all(isinstance(x, dict) and {"file", "line", "body"} <= set(x) for x in d)
PY

READY_DATA="$(python3 -c 'import json,sys;print(json.dumps({"run_dir":sys.argv[1],"session_id":sys.argv[2],"cwd":sys.argv[3]}))' "$RUN_DIR" "$SESSION_ID" "$LAUNCH_CWD")"
event review_ready --data "$READY_DATA"
run_alerts
echo "review ready: $RUN_DIR"
exit 0
