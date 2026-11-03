#!/usr/bin/env bash
# Tests for review-watcher/launchd.sh and cmux-probe.sh.
# launchctl and cmux are fake binaries; nothing touches the real LaunchAgents dir.
#
# Usage: bash test-launchd.sh
set -uo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$SCRIPTS_DIR/review-watcher/launchd.sh"
EVENTS="$SCRIPTS_DIR/review-watcher/events.py"
PASS=0; FAIL=0
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
assert_pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
assert_eq() { if [ "$1" = "$2" ]; then assert_pass "$3"; else assert_fail "$3 (expected '$2', got '$1')"; fi; }
assert_contains() { if printf '%s' "$1" | grep -qF -- "$2"; then assert_pass "$3"; else assert_fail "$3 ('$2' not in: $1)"; fi; }

mkdir -p "$TMP/bin"
cat > "$TMP/bin/launchctl" <<'F'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
if [ "$1" = "print" ]; then [ -f "$FAKE_LOADED" ] && echo "state = running" && exit 0; exit 113; fi
if [ "$1" = "bootstrap" ]; then touch "$FAKE_LOADED"; fi
if [ "$1" = "bootout" ]; then rm -f "$FAKE_LOADED"; fi
exit 0
F
cat > "$TMP/bin/cmux" <<'F'
#!/usr/bin/env bash
case "${FAKE_CMUX_MODE:-ok}" in ok) echo pong; exit 0 ;; fail) echo "socket refused" >&2; exit 1 ;; hang) sleep 30 ;; esac
F
# plutil ships with macOS only. Where it is missing (Linux CI), stand in a small plistlib-based
# shim covering the two forms launchd.sh and this test use: `-lint file` and `-extract key raw file`.
if ! command -v plutil >/dev/null 2>&1; then
  cat > "$TMP/bin/plutil" <<'F'
#!/usr/bin/env python3
import plistlib, sys
a = sys.argv[1:]
try:
    if a[0] == "-lint":
        plistlib.load(open(a[-1], "rb")); sys.exit(0)
    if a[0] == "-extract" and a[2] == "raw":
        node = plistlib.load(open(a[3], "rb"))
        for part in a[1].split("."):
            node = node[int(part)] if isinstance(node, list) else node[part]
        print("true" if node is True else "false" if node is False else node)
        sys.exit(0)
except Exception as e:
    print(e, file=sys.stderr); sys.exit(1)
sys.exit(2)
F
fi
chmod +x "$TMP/bin/"*
export PATH="$TMP/bin:$PATH"

export FAKE_LOG="$TMP/launchctl.log" FAKE_LOADED="$TMP/loaded"
export REVIEW_WATCHER_LAUNCHCTL="$TMP/bin/launchctl"
export REVIEW_WATCHER_LAUNCH_AGENTS_DIR="$TMP/agents"
export REVIEW_WATCHER_HOME="$TMP/state"
export REVIEW_WATCHER_CMUX_BIN="$TMP/bin/cmux"
export REVIEW_WATCHER_PROBE_TIMEOUT=1
UID_N=$(id -u)
PLIST="$TMP/agents/local.review-watcher.plist"
LOG="$TMP/state/events.jsonl"

echo "install"
out=$(bash "$SCRIPT" install 2>&1); rc=$?
assert_eq "$rc" "0" "install exits 0"
assert_eq "$(plutil -lint "$PLIST" >/dev/null 2>&1; echo $?)" "0" "plist passes plutil -lint"
assert_eq "$(plutil -extract StartInterval raw "$PLIST")" "600" "StartInterval is 600"
assert_eq "$(plutil -extract RunAtLoad raw "$PLIST")" "true" "RunAtLoad true"
assert_eq "$(plutil -extract ProgramArguments.0 raw "$PLIST")" "/bin/bash" "runs /bin/bash"
wsh="$(plutil -extract ProgramArguments.1 raw "$PLIST")"
case "$wsh" in /*/review-watcher/watcher.sh) assert_pass "watcher.sh path is absolute" ;; *) assert_fail "watcher.sh path ($wsh)" ;; esac
if [ -f "$wsh" ]; then assert_pass "watcher.sh exists at that path"; else assert_fail "watcher.sh missing"; fi
assert_contains "$(plutil -extract EnvironmentVariables.PATH raw "$PLIST")" "/opt/homebrew/bin" "PATH has homebrew"
assert_contains "$(plutil -extract EnvironmentVariables.PATH raw "$PLIST")" ".local/bin" "PATH has ~/.local/bin"
assert_eq "$(plutil -extract EnvironmentVariables.HOME raw "$PLIST")" "$HOME" "HOME set"
assert_eq "$(plutil -extract StandardOutPath raw "$PLIST")" "$TMP/state/launchd.out.log" "stdout log path"
assert_eq "$(plutil -extract StandardErrorPath raw "$PLIST")" "$TMP/state/launchd.err.log" "stderr log path"
calls=$(cat "$FAKE_LOG")
assert_contains "$calls" "bootstrap gui/$UID_N $PLIST" "launchctl bootstrap called with gui domain"
assert_contains "$calls" "submit -l local.review-watcher.probe" "probe submitted under launchd"
assert_contains "$calls" "REVIEW_WATCHER_PROBE_SELF_REMOVE=local.review-watcher.probe" "probe told to remove its own job"

echo "status"
out=$(bash "$SCRIPT" status 2>&1); rc=$?
assert_eq "$rc" "0" "status exits 0 when loaded"
assert_contains "$(cat "$FAKE_LOG")" "print gui/$UID_N/local.review-watcher" "status uses launchctl print"
assert_contains "$out" "state = running" "status shows job"

echo "probe"
for mode in ok fail hang; do
  rm -f "$LOG"
  FAKE_CMUX_MODE=$mode bash "$SCRIPT" probe >/dev/null 2>&1
  ev=$(tail -1 "$LOG" | python3 -c "import json,sys; e=json.load(sys.stdin); print(e['type'],e['repo'],e['pr'],e['data']['reachable'])")
  case $mode in ok) want="cmux_probe _system 0 True" ;; *) want="cmux_probe _system 0 False" ;; esac
  assert_eq "$ev" "$want" "probe event recorded ($mode)"
done
rm -f "$LOG"; REVIEW_WATCHER_CMUX_BIN="$TMP/nope" bash "$SCRIPT" probe >/dev/null 2>&1
assert_contains "$(cat "$LOG")" '"reachable":false' "missing cmux binary recorded as unreachable"
st=$(python3 "$EVENTS" state --json | tr -d ' \n'); assert_eq "$st" "[]" "probe event does not affect queue state"
: > "$FAKE_LOG"
REVIEW_WATCHER_PROBE_SELF_REMOVE=local.review-watcher.probe FAKE_CMUX_MODE=ok \
  bash "$SCRIPTS_DIR/review-watcher/cmux-probe.sh" >/dev/null 2>&1
assert_contains "$(cat "$FAKE_LOG")" "remove local.review-watcher.probe" "probe removes its own submit job"

echo "uninstall"
python3 "$EVENTS" append request_seen --repo a/b --pr 1 >/dev/null
before=$(cat "$LOG")
out=$(bash "$SCRIPT" uninstall 2>&1); rc=$?
assert_eq "$rc" "0" "uninstall exits 0"
if [ ! -e "$PLIST" ]; then assert_pass "plist removed"; else assert_fail "plist still present"; fi
assert_contains "$(cat "$FAKE_LOG")" "bootout gui/$UID_N/local.review-watcher" "launchctl bootout called"
assert_eq "$(cat "$LOG")" "$before" "event log untouched"
out=$(bash "$SCRIPT" status 2>&1); rc=$?
assert_eq "$rc" "1" "status fails after uninstall"

echo; echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
