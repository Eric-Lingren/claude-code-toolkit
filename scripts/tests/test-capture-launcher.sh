#!/usr/bin/env bash
# Tests for capture/capture (hotkey launcher). osascript and claude are stubbed; nothing real is launched.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
S="$HERE/../capture/capture"
TMP="$(mktemp -d)"
trap 'rm -r "$TMP"' EXIT
mkdir -p "$TMP/work"
cat > "$TMP/osa" <<'SHIM'
#!/usr/bin/env bash
echo "osa $*" >> "$LOG"
case "$*" in *"frontmost"*) echo "Ghostty" ;; esac
SHIM
cat > "$TMP/claude" <<'SHIM'
#!/usr/bin/env bash
echo "claude cfg=$CLAUDE_CONFIG_DIR pwd=$PWD args=$*" >> "$LOG"
[ -z "${STUB_RESULT:-}" ] || echo "$STUB_RESULT" > "$CAPTURE_RESULT_FILE"
SHIM
chmod +x "$TMP/osa" "$TMP/claude"
export LOG="$TMP/log" CAPTURE_OSASCRIPT="$TMP/osa" CAPTURE_CLAUDE="$TMP/claude" CAPTURE_DIR="$TMP/work" CAPTURE_CCH_DIR="$TMP/cch"
FAILS=0
fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }
pass() { echo "PASS: $1"; }

# 1. frontmost app recorded before Terminal opens
: > "$LOG"; unset STUB_RESULT
"$S" some idea text >/dev/null
first="$(sed -n 1p "$LOG")"; second="$(sed -n 2p "$LOG")"
case "$first" in *frontmost*) pass "frontmost queried first" ;; *) fail "first call: $first" ;; esac
case "$second" in *'tell application "Terminal"'*'do script'*"--inner Ghostty"*) pass "Terminal opened with recorded app" ;; *) fail "second call: $second" ;; esac
[ "$(wc -l < "$LOG")" -eq 2 ] && pass "no claude run by outer launcher" || fail "unexpected calls"
case "$second" in *'NSScreen'*'mainScreen'*'set bounds of front window'*) pass "Terminal window centered on main screen" ;; *) fail "no centering: $second" ;; esac

# 1b. trailing newlines from Ask for Input are trimmed
: > "$LOG"; unset STUB_RESULT
"$S" --inner Ghostty "$TMP/res0" $'test idea\n\n' >/dev/null
grep -q 'args=--model haiku --effort low /capture test idea$' "$LOG" && pass "trailing newlines trimmed" || fail "trim: $(cat "$LOG")"

# 2. success quits Terminal and re-activates
: > "$LOG"; export STUB_RESULT='✓ captured #5 [idea] https://x/issues/5'
"$S" --inner Ghostty "$TMP/res1" hello world >/dev/null
if grep -q 'quit' "$LOG" && grep -q 'application "Ghostty" to activate' "$LOG"; then
  pass "success quits Terminal and activates Ghostty"
else fail "success calls: $(cat "$LOG")"; fi

# 3. ERROR leaves Terminal open, no re-activate
: > "$LOG"; export STUB_RESULT='ERROR: gh auth expired'
out="$("$S" --inner Ghostty "$TMP/res2" boom)"
if grep -q 'quit\|activate' "$LOG"; then fail "ERROR path touched Terminal: $(cat "$LOG")"; else pass "ERROR leaves Terminal open"; fi
case "$out" in *"ERROR: gh auth expired"*) pass "ERROR shown" ;; *) fail "out: $out" ;; esac

# 3b. no result file also stays open
: > "$LOG"; unset STUB_RESULT
"$S" --inner Ghostty "$TMP/res3" x >/dev/null
if grep -q 'quit\|activate' "$LOG"; then fail "missing result closed Terminal"; else pass "missing result leaves Terminal open"; fi

# 4. cch profile regardless of current profile; haiku low; in work dir
: > "$LOG"; unset STUB_RESULT
CLAUDE_CONFIG_DIR="$TMP/cco" "$S" --inner Ghostty "$TMP/res4" fix the thing >/dev/null
l="$(grep '^claude' "$LOG")"
case "$l" in *"cfg=$TMP/cch "*) pass "runs on cch profile" ;; *) fail "profile: $l" ;; esac
case "$l" in *"--model haiku --effort low /capture fix the thing"*) pass "haiku low effort with text" ;; *) fail "args: $l" ;; esac
case "$l" in *"pwd=$(cd "$TMP/work" && pwd -P)"*|*"pwd=$TMP/work"*) pass "runs in dotfiles dir" ;; *) fail "pwd: $l" ;; esac

[ "$FAILS" -eq 0 ] && echo "all passed" || { echo "$FAILS failed"; exit 1; }
