#!/usr/bin/env bash
# Tests for notify/notify.sh
# terminal-notifier is replaced by a fake binary on PATH. The fake records its
# arguments, models "same -group replaces the banner", and can simulate a click.
# No real macOS banners are ever raised.
#
# Usage: bash test-notify.sh
# Exit 0 = all tests passed. Exit 1 = one or more failed.

set -uo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$SCRIPTS_DIR/notify/notify.sh"

PASS=0
FAIL=0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

assert_pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

assert_eq() {
  if [ "$1" = "$2" ]; then assert_pass "$3"; else assert_fail "$3 (expected '$2', got '$1')"; fi
}
assert_contains() {
  if printf '%s' "$1" | grep -qF -- "$2"; then assert_pass "$3"; else assert_fail "$3 ('$2' not in: $1)"; fi
}
assert_nonzero() {
  if [ "$1" -ne 0 ]; then assert_pass "$2"; else assert_fail "$2 (exited 0)"; fi
}

# --- Fake terminal-notifier ---
FAKE_BIN="$TMP/bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/terminal-notifier" <<'FAKE'
#!/usr/bin/env bash
# Records one line per call (args joined by US char), keeps one "banner" file
# per -group, and simulates a click on -execute when FAKE_TN_CLICK=1.
{
  sep=""
  for a in "$@"; do printf '%s%s' "$sep" "$a"; sep=$'\x1f'; done
  printf '\n'
} >> "$FAKE_TN_DIR/calls.log"
group="" execute="" title="" message=""
while [ $# -gt 0 ]; do
  case "$1" in
    -group) group="$2"; shift 2 ;;
    -execute) execute="$2"; shift 2 ;;
    -title) title="$2"; shift 2 ;;
    -message) message="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$FAKE_TN_DIR/banners"
key="${group:-__nogroup_$RANDOM$RANDOM$RANDOM}"
printf '%s\n%s\n' "$title" "$message" > "$FAKE_TN_DIR/banners/$key"
if [ "${FAKE_TN_CLICK:-0}" = "1" ] && [ -n "$execute" ]; then
  sh -c "$execute"
fi
exit 0
FAKE
chmod +x "$FAKE_BIN/terminal-notifier"

new_env() { export FAKE_TN_DIR="$TMP/run-$1"; mkdir -p "$FAKE_TN_DIR"; }
run_notify() { PATH="$FAKE_BIN:$PATH" bash "$SCRIPT" "$@"; }
call_args() { sed -n "${1}p" "$FAKE_TN_DIR/calls.log" | tr '\037' '\n'; }
banner_count() { ls "$FAKE_TN_DIR/banners" | wc -l | tr -d ' '; }

# --- T1: script exists and is executable ---
echo "=== T1: script exists ==="
if [ -x "$SCRIPT" ]; then assert_pass "notify.sh is executable"; else assert_fail "notify.sh missing or not executable"; fi

# --- T2: basic pass-through ---
echo "=== T2: title/message passed through ==="
new_env 2
run_notify --title "Hello" --message "World" >/dev/null 2>&1
assert_eq "$?" "0" "exits 0"
args=$(call_args 1)
assert_contains "$args" "-title" "passes -title"
assert_contains "$args" "Hello" "passes title value"
assert_contains "$args" "World" "passes message value"
assert_eq "$(wc -l < "$FAKE_TN_DIR/calls.log" | tr -d ' ')" "1" "terminal-notifier called once"

# --- T3: same group leaves one banner ---
echo "=== T3: same --group leaves one banner ==="
new_env 3
run_notify --title "PR #1" --message "first" --group review-watcher >/dev/null 2>&1
run_notify --title "PR #1" --message "second" --group review-watcher >/dev/null 2>&1
a1=$(call_args 1); a2=$(call_args 2)
assert_contains "$a1" "-group" "first call passes -group"
assert_contains "$a1" "review-watcher" "first call group value"
assert_contains "$a2" "review-watcher" "second call uses same group"
assert_eq "$(banner_count)" "1" "one banner on screen after two same-group calls"
assert_contains "$(cat "$FAKE_TN_DIR/banners/review-watcher")" "second" "banner holds latest message"

echo "=== T3b: different groups keep separate banners ==="
new_env 31
run_notify --title "A" --message "a" --group g1 >/dev/null 2>&1
run_notify --title "B" --message "b" --group g2 >/dev/null 2>&1
assert_eq "$(banner_count)" "2" "two banners for two groups"

echo "=== T3c: no --group omits -group ==="
new_env 32
run_notify --title "A" --message "a" >/dev/null 2>&1
case "$(call_args 1)" in
  *-group*) assert_fail "-group should be absent" ;;
  *) assert_pass "-group absent when not given" ;;
esac

# --- T4: click runs --execute command ---
echo "=== T4: clicking banner runs --execute command ==="
new_env 4
MARK="$TMP/clicked"
FAKE_TN_CLICK=1 run_notify --title "T" --message "M" --execute "touch '$MARK'" >/dev/null 2>&1
assert_contains "$(call_args 1)" "-execute" "passes -execute"
if [ -f "$MARK" ]; then assert_pass "execute command ran on simulated click"; else assert_fail "execute command did not run"; fi
assert_contains "$(call_args 1)" "touch '$MARK'" "execute value passed verbatim"

# --- T5: --open ---
echo "=== T5: --open passes -open ==="
new_env 5
run_notify --title "T" --message "M" --open "https://example.com/pr/1" >/dev/null 2>&1
args=$(call_args 1)
assert_contains "$args" "-open" "passes -open"
assert_contains "$args" "https://example.com/pr/1" "passes url"

# --- T6: usage errors ---
echo "=== T6: usage errors ==="
new_env 6
out=$(run_notify --message "M" 2>&1); code=$?
assert_nonzero "$code" "missing --title exits non-zero"
assert_contains "$out" "Usage:" "missing --title prints usage"
assert_contains "$out" "--title" "mentions --title"
out=$(run_notify --title "T" 2>&1); code=$?
assert_nonzero "$code" "missing --message exits non-zero"
assert_contains "$out" "Usage:" "missing --message prints usage"
out=$(run_notify 2>&1); code=$?
assert_nonzero "$code" "no args exits non-zero"
if [ ! -f "$FAKE_TN_DIR/calls.log" ]; then assert_pass "terminal-notifier not called on usage error"; else assert_fail "terminal-notifier called on usage error"; fi
out=$(run_notify --title 2>&1); code=$?
assert_nonzero "$code" "flag without value exits non-zero"
out=$(run_notify --title T --message M --bogus 2>&1); code=$?
assert_nonzero "$code" "unknown flag exits non-zero"
out=$(run_notify --title T --message M --execute x --open y 2>&1); code=$?
assert_nonzero "$code" "--execute with --open exits non-zero"

# --- T7: terminal-notifier missing ---
echo "=== T7: terminal-notifier not installed ==="
EMPTY_BIN="$TMP/empty"; mkdir -p "$EMPTY_BIN"
BASH_BIN="$(command -v bash)"
out=$(PATH="$EMPTY_BIN" "$BASH_BIN" "$SCRIPT" --title T --message M 2>&1); code=$?
assert_eq "$code" "127" "exits 127 when terminal-notifier missing"
assert_contains "$out" "terminal-notifier" "error names terminal-notifier"

# --- T8: Brewfile ---
echo "=== T8: Brewfile has terminal-notifier ==="
BREWFILE="$(cd "$SCRIPTS_DIR/../.." && pwd)/Brewfile"
if grep -q '^brew "terminal-notifier"' "$BREWFILE"; then assert_pass "Brewfile lists terminal-notifier"; else assert_fail "Brewfile missing terminal-notifier"; fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
