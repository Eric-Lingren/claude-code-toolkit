#!/usr/bin/env bash
# find-work.test.sh — tests for find-work.sh gxlist delegation behavior.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SCRIPT_DIR/find-work.sh"

PASS=0
FAIL=0
FAILURES=()

# ─── Helpers ──────────────────────────────────────────────────────────────────

pass() { echo "  PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "  FAIL: $1"; FAIL=$(( FAIL + 1 )); FAILURES+=("$1"); }

assert_contains() {
  local label="$1" expected="$2" actual="$3"
  if echo "$actual" | grep -qF "$expected"; then
    pass "$label"
  else
    fail "$label — expected: '$expected' in: '$actual'"
  fi
}

assert_not_contains() {
  local label="$1" unexpected="$2" actual="$3"
  if echo "$actual" | grep -qF "$unexpected"; then
    fail "$label — NOT expected: '$unexpected' in: '$actual'"
  else
    pass "$label"
  fi
}

# Temp dir for mock binaries
TMPDIR_TESTS="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_TESTS"' EXIT

# ─── Test 1: gxlist called when invoked with no args ──────────────────────────

echo "Test 1: gxlist is invoked when called with no args"

MOCK_GXLIST1="$TMPDIR_TESTS/gxlist_1"
MOCK_ARGS_FILE1="$TMPDIR_TESTS/args_1.txt"
cat > "$MOCK_GXLIST1" <<'EOF'
#!/bin/bash
echo "gxlist_called" >> "$MOCK_ARGS_FILE_PATH"
printf '%s\n' "$@" >> "$MOCK_ARGS_FILE_PATH"
echo "READY"
echo "  My ticket  [feat/my-ticket]  https://linear.app/x/XY-1"
EOF
chmod +x "$MOCK_GXLIST1"

# Use env to properly propagate vars into the command substitution subshell
output=$(env MOCK_ARGS_FILE_PATH="$MOCK_ARGS_FILE1" GXLIST_PATH="$MOCK_GXLIST1" bash "$SCRIPT" 2>&1) || true

if [[ -f "$MOCK_ARGS_FILE1" ]] && grep -q "gxlist_called" "$MOCK_ARGS_FILE1"; then
  pass "gxlist binary was invoked"
else
  fail "gxlist binary was not invoked (args file: $(cat "$MOCK_ARGS_FILE1" 2>/dev/null || echo 'missing'))"
fi

assert_contains "gxlist output passed through (READY)" "READY" "$output"
assert_contains "gxlist output passed through (ticket)" "My ticket" "$output"

# ─── Test 2: --all is forwarded when passed ────────────────────────────────────

echo "Test 2: --all flag is forwarded to gxlist"

MOCK_GXLIST2="$TMPDIR_TESTS/gxlist_2"
MOCK_ARGS_FILE2="$TMPDIR_TESTS/args_2.txt"
cat > "$MOCK_GXLIST2" <<'EOF'
#!/bin/bash
printf '%s\n' "$@" > "$MOCK_ARGS_FILE_PATH"
echo "No active worktrees."
EOF
chmod +x "$MOCK_GXLIST2"

MOCK_ARGS_FILE_PATH="$MOCK_ARGS_FILE2" \
  GXLIST_PATH="$MOCK_GXLIST2" \
  bash "$SCRIPT" --all >/dev/null 2>&1 || true

if [[ -f "$MOCK_ARGS_FILE2" ]] && grep -q "^--all$" "$MOCK_ARGS_FILE2"; then
  pass "--all forwarded to gxlist"
else
  fail "--all NOT forwarded to gxlist (args file: $(cat "$MOCK_ARGS_FILE2" 2>/dev/null || echo 'missing'))"
fi

# ─── Test 3: no --all arg when not provided ────────────────────────────────────

echo "Test 3: --all is not sent when not provided"

MOCK_GXLIST3="$TMPDIR_TESTS/gxlist_3"
MOCK_ARGS_FILE3="$TMPDIR_TESTS/args_3.txt"
cat > "$MOCK_GXLIST3" <<'EOF'
#!/bin/bash
printf '%s\n' "$@" > "$MOCK_ARGS_FILE_PATH"
echo "No active worktrees."
EOF
chmod +x "$MOCK_GXLIST3"

MOCK_ARGS_FILE_PATH="$MOCK_ARGS_FILE3" \
  GXLIST_PATH="$MOCK_GXLIST3" \
  bash "$SCRIPT" >/dev/null 2>&1 || true

if [[ -f "$MOCK_ARGS_FILE3" ]] && grep -q "^--all$" "$MOCK_ARGS_FILE3"; then
  fail "--all was sent when it should NOT have been"
else
  pass "--all correctly absent when not requested"
fi

# ─── Test 4: fallback when gxlist is absent ────────────────────────────────────

echo "Test 4: falls back to branch scanning when gxlist is absent"

fallback_output=$(
  env GXLIST_PATH="$TMPDIR_TESTS/nonexistent_gxlist" bash "$SCRIPT" 2>&1
) || true

assert_contains "warns about missing gxlist" "gxlist not found" "$fallback_output"
assert_not_contains "no gxlist READY header in fallback" "READY" "$fallback_output"
assert_not_contains "no gxlist BUILDING header in fallback" "BUILDING" "$fallback_output"

# ─── Test 5: find-work.sh is executable ───────────────────────────────────────

echo "Test 5: find-work.sh is executable"
if [[ -x "$SCRIPT" ]]; then
  pass "find-work.sh is executable"
else
  fail "find-work.sh is not executable"
fi

# ─── Summary ──────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [[ "$FAIL" -gt 0 ]]; then
  echo "Failed tests:"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  exit 1
fi

exit 0
