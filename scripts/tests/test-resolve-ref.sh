#!/usr/bin/env bash
# Tests for resolve-ref.sh — active-file hit, archive hit, not-found hard-fail.
set -euo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RESOLVER="$SCRIPTS_DIR/scaffolding/resolve-ref.sh"

TMP=$(mktemp -d)
PASS=0
FAIL=0
CASE_STDOUT="$TMP/case_stdout"
CASE_STDERR="$TMP/case_stderr"

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

assert_pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

# run_case: calls resolver, checks exit code, writes stdout/stderr to CASE_STDOUT/CASE_STDERR
run_case() {
  local name="$1"
  local expect_exit="$2"
  shift 2
  set +e
  "$RESOLVER" "$@" >"$CASE_STDOUT" 2>"$CASE_STDERR"
  local actual_exit=$?
  set -e
  if [ "$expect_exit" -eq 0 ] && [ "$actual_exit" -eq 0 ]; then
    assert_pass "$name: exited 0"
  elif [ "$expect_exit" -ne 0 ] && [ "$actual_exit" -ne 0 ]; then
    assert_pass "$name: exited non-zero (correctly rejected)"
  elif [ "$expect_exit" -eq 0 ] && [ "$actual_exit" -ne 0 ]; then
    assert_fail "$name: expected exit 0 but got $actual_exit. stderr: $(cat "$CASE_STDERR")"
  else
    assert_fail "$name: expected non-zero but exited 0. stdout: $(cat "$CASE_STDOUT")"
  fi
}

# --- Fixture setup ---
mkdir -p "$TMP/docs/seeds" "$TMP/docs/tasks" "$TMP/docs/archive"
echo '{"schema_version":"4","title":"Active seed"}' > "$TMP/docs/seeds/20260101-1200-active-seed.json"
echo '{"schema_version":"2","title":"Active task"}' > "$TMP/docs/tasks/20260101-1201-active-task.json"

cat > "$TMP/docs/archive/20260101-0001-archived-feature.json" <<'EOF'
{
  "kind": "condensed",
  "producer": "clean-scaffolding",
  "slug": "archived-feature",
  "title": "Archived Feature",
  "condensed_at": "2026-01-02T00:00:00Z",
  "condensed_from": [
    "20260101-0900-archived-seed.json",
    "20260101-0910-archived-task.json"
  ],
  "artifacts": [
    {
      "filename": "20260101-0900-archived-seed.json",
      "type": "seed",
      "content": {"schema_version": "4", "title": "Archived seed content", "slug": "archived-feature"}
    },
    {
      "filename": "20260101-0910-archived-task.json",
      "type": "task",
      "content": {"schema_version": "2", "title": "Archived task content"}
    }
  ]
}
EOF

export RESOLVE_REF_DOCS_ROOT="$TMP/docs"

echo "=== resolve-ref.sh tests ==="

# Case 1: Active seed hit — returns file path
run_case "Case 1 (active seed hit exits 0)" 0 "20260101-1200-active-seed.json"
ACTUAL=$(cat "$CASE_STDOUT")
if echo "$ACTUAL" | grep -q "20260101-1200-active-seed.json"; then
  assert_pass "Case 1: stdout contains active file path"
else
  assert_fail "Case 1: stdout did not contain file path. Got: $ACTUAL"
fi
if head -1 "$CASE_STDOUT" | grep -q "^ARCHIVE:"; then
  assert_fail "Case 1: stdout should not start with ARCHIVE: for active hit"
else
  assert_pass "Case 1: stdout does not start with ARCHIVE: (active hit)"
fi

# Case 2: Active task hit
run_case "Case 2 (active task hit exits 0)" 0 "20260101-1201-active-task.json"
if cat "$CASE_STDOUT" | grep -q "20260101-1201-active-task.json"; then
  assert_pass "Case 2: stdout contains active task path"
else
  assert_fail "Case 2: stdout did not contain task path. Got: $(cat "$CASE_STDOUT")"
fi

# Case 3: Archive hit — ARCHIVE: prefix, bundle path, artifact content
run_case "Case 3 (archive seed hit exits 0)" 0 "20260101-0900-archived-seed.json"
FIRST_LINE=$(head -1 "$CASE_STDOUT")
if echo "$FIRST_LINE" | grep -q "^ARCHIVE:"; then
  assert_pass "Case 3: first line starts with ARCHIVE:"
else
  assert_fail "Case 3: first line should start with ARCHIVE:. Got: $FIRST_LINE"
fi
if grep -q "20260101-0001-archived-feature.json" "$CASE_STDOUT"; then
  assert_pass "Case 3: bundle path present in output"
else
  assert_fail "Case 3: bundle path missing. Got: $(cat "$CASE_STDOUT")"
fi
if grep -q "Archived seed content" "$CASE_STDOUT"; then
  assert_pass "Case 3: artifact content JSON present"
else
  assert_fail "Case 3: artifact content missing. Got: $(cat "$CASE_STDOUT")"
fi

# Case 4: Archive hit — second artifact in same bundle
run_case "Case 4 (archive task hit exits 0)" 0 "20260101-0910-archived-task.json"
if head -1 "$CASE_STDOUT" | grep -q "^ARCHIVE:" && grep -q "Archived task content" "$CASE_STDOUT"; then
  assert_pass "Case 4: archive task hit returns ARCHIVE: prefix and content"
else
  assert_fail "Case 4: archive task hit incorrect. Got: $(cat "$CASE_STDOUT")"
fi

# Case 5: Not found — exit 1, structured diagnostic on stderr
run_case "Case 5 (not found exits non-zero)" 1 "does-not-exist.json"
if grep -q "does-not-exist.json" "$CASE_STDERR" && grep -q "Basename sought" "$CASE_STDERR"; then
  assert_pass "Case 5: stderr contains basename and 'Basename sought'"
else
  assert_fail "Case 5: stderr missing expected diagnostic. Got: $(cat "$CASE_STDERR")"
fi
if grep -q "pipeline-integrity bug" "$CASE_STDERR"; then
  assert_pass "Case 5: stderr contains pipeline-integrity note"
else
  assert_fail "Case 5: stderr missing pipeline-integrity note. Got: $(cat "$CASE_STDERR")"
fi
if grep -q "Active dirs searched" "$CASE_STDERR"; then
  assert_pass "Case 5: stderr names active dirs searched"
else
  assert_fail "Case 5: stderr missing 'Active dirs searched'"
fi
if grep -q "Archive bundles scanned" "$CASE_STDERR"; then
  assert_pass "Case 5: stderr names archive bundles scanned"
else
  assert_fail "Case 5: stderr missing 'Archive bundles scanned'"
fi

# Case 6: No archive dir — reports 0 bundles
NO_ARCHIVE_TMP=$(mktemp -d)
mkdir -p "$NO_ARCHIVE_TMP/docs/seeds"
set +e
STDERR6=$(RESOLVE_REF_DOCS_ROOT="$NO_ARCHIVE_TMP/docs" "$RESOLVER" "missing.json" 2>&1 || true)
set -e
rm -rf "$NO_ARCHIVE_TMP"
if echo "$STDERR6" | grep -q "0 bundle(s)"; then
  assert_pass "Case 6: 0 bundles reported when archive dir absent"
else
  assert_fail "Case 6: expected '0 bundle(s)' in stderr. Got: $STDERR6"
fi

# Case 7: Active file takes priority over same basename in archive bundle
python3 - "$TMP/docs/archive/20260101-0002-overlap-test.json" <<'PYEOF'
import json
bundle = {
  "kind": "condensed",
  "producer": "clean-scaffolding",
  "slug": "overlap-test",
  "title": "Overlap test",
  "condensed_at": "2026-01-02T01:00:00Z",
  "condensed_from": ["20260101-1200-active-seed.json"],
  "artifacts": [
    {"filename": "20260101-1200-active-seed.json", "type": "seed",
     "content": {"title": "Should not appear — active wins"}}
  ]
}
import sys
with open(sys.argv[1], "w") as f:
    json.dump(bundle, f)
PYEOF
run_case "Case 7 (active wins over archive for same basename)" 0 "20260101-1200-active-seed.json"
if head -1 "$CASE_STDOUT" | grep -q "^ARCHIVE:"; then
  assert_fail "Case 7: active file should win but got ARCHIVE hit. stdout: $(cat "$CASE_STDOUT")"
else
  assert_pass "Case 7: active file path returned (active takes priority over archive)"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
