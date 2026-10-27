#!/usr/bin/env bash
# Deterministic fixtures test for validate-schema.sh.
# Tests schema self-validation (examples[] are checked against the schema itself),
# the backward-compat 2-arg guard, and rejection of bad embedded examples.
set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONTRACTS_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)/contracts"
VALIDATOR="$SCRIPTS_DIR/shared/validate-schema.sh"
SCHEMA="$CONTRACTS_DIR/task-schema.json"
TMP=$(mktemp -d)

PASS=0
FAIL=0

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

assert_pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

run_case() {
  local name="$1"
  local expect_exit="$2"
  shift 2

  set +e
  "$VALIDATOR" "$@" 2>/tmp/validate-stderr
  local actual_exit=$?
  set -e

  if [ "$expect_exit" -eq 0 ] && [ "$actual_exit" -eq 0 ]; then
    assert_pass "$name: exited 0 (valid)"
  elif [ "$expect_exit" -ne 0 ] && [ "$actual_exit" -ne 0 ]; then
    assert_pass "$name: exited non-zero (correctly rejected)"
  elif [ "$expect_exit" -eq 0 ] && [ "$actual_exit" -ne 0 ]; then
    assert_fail "$name: expected exit 0 but got $actual_exit. stderr: $(cat /tmp/validate-stderr)"
  else
    assert_fail "$name: expected non-zero but exited 0 (should have been rejected)"
  fi
}

echo "=== validate-schema.sh fixtures test ==="

# Case 1: valid schema — task-schema.json has one embedded example that should pass
run_case "Case 1 (task-schema.json valid example)" 0 "$SCHEMA"

# Case 2: backward-compat guard — 2 file args (old API form) must exit non-zero
DUMMY="$TMP/dummy.json"
echo '{}' > "$DUMMY"
run_case "Case 2 (2-arg old form exits non-zero)" 1 "$SCHEMA" "$DUMMY"

# Case 3: schema with a bad embedded example (missing required field in example)
BAD_MISSING="$TMP/bad-missing.json"
cat > "$BAD_MISSING" <<'EOF'
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "test-missing",
  "type": "object",
  "required": ["required_field"],
  "additionalProperties": false,
  "properties": {
    "required_field": {"type": "string"}
  },
  "examples": [
    {}
  ]
}
EOF
run_case "Case 3 (embedded example missing required field)" 1 "$BAD_MISSING"

# Case 4: schema with a bad embedded example (extra undeclared field)
BAD_EXTRA="$TMP/bad-extra.json"
cat > "$BAD_EXTRA" <<'EOF'
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "test-extra",
  "type": "object",
  "required": ["name"],
  "additionalProperties": false,
  "properties": {
    "name": {"type": "string"}
  },
  "examples": [
    {"name": "valid", "undeclared_field": "should be rejected"}
  ]
}
EOF
run_case "Case 4 (embedded example has extra undeclared field)" 1 "$BAD_EXTRA"

# Case 5: valid schema with correct embedded example (clean pass)
GOOD_SCHEMA="$TMP/good-schema.json"
cat > "$GOOD_SCHEMA" <<'EOF'
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "test-good",
  "type": "object",
  "required": ["name"],
  "additionalProperties": false,
  "properties": {
    "name": {"type": "string"}
  },
  "examples": [
    {"name": "hello"}
  ]
}
EOF
run_case "Case 5 (schema with valid embedded example)" 0 "$GOOD_SCHEMA"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
