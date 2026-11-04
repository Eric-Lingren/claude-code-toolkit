#!/usr/bin/env bash
# test-run-all-tests.sh — Tests for the repo-root run-all-tests.sh runner
# What: builds temp fixture trees (passing and failing bash + pytest files in every suite location) and asserts the runner's per-file PASS/FAIL lines, summary count, and exit code
# When: run via run-all-tests.sh itself; safe from recursion because the runner is pointed at a fixture root, never the real repo

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNNER="$REPO_ROOT/run-all-tests.sh"

PASS=0
FAIL=0
TMP="$(mktemp -d)"
trap 'rm -r "$TMP"' EXIT

check() {
  local name="$1" ok="$2"
  if [[ "$ok" == "1" ]]; then
    echo "  ok: $name"; PASS=$((PASS + 1))
  else
    echo "  FAIL: $name"; FAIL=$((FAIL + 1))
  fi
}

has_line() { grep -qxF -- "$2" <<<"$1" && echo 1 || echo 0; }

write_bash() { # path exit_code
  mkdir -p "$(dirname "$1")"
  printf '#!/usr/bin/env bash\nexit %s\n' "$2" >"$1"
}
write_py() { # path assert_expr
  mkdir -p "$(dirname "$1")"
  printf 'def test_it():\n    assert %s\n' "$2" >"$1"
}

# Layout mirrors the real repo: gx suite, characterization suite, shared scripts/tests.
build_tree() { # root, mode: pass|fail
  local root="$1" mode="$2" bad=0 badpy="True"
  if [[ "$mode" == "fail" ]]; then bad=1; badpy="False"; fi
  write_bash "$root/.scripts/foo.test.sh" 0
  write_bash "$root/.scripts/tests/test-gx-ok.sh" 0
  write_bash "$root/.scripts/tests/test-gx-maybe.sh" "$bad"
  write_bash "$root/claude-code-shared/tests/characterization/test-adapter-ok.sh" 0
  write_bash "$root/claude-code-shared/tests/characterization/test-adapter-maybe.sh" "$bad"
  write_bash "$root/claude-code-shared/scripts/tests/test-shared-ok.sh" 0
  write_bash "$root/claude-code-shared/scripts/tests/test-shared-maybe.sh" "$bad"
  write_py "$root/claude-code-shared/scripts/tests/test_py_ok.py" "True"
  write_py "$root/claude-code-shared/scripts/tests/test_py_maybe.py" "$badpy"
}

echo "== all-pass tree =="
build_tree "$TMP/pass" pass
out="$(bash "$RUNNER" "$TMP/pass" 2>&1)"; rc=$?
check "exit 0 when every file passes" "$([[ $rc -eq 0 ]] && echo 1 || echo 0)"
check "gx root test PASS line" "$(has_line "$out" 'PASS: .scripts/foo.test.sh')"
check "gx tests/ PASS line" "$(has_line "$out" 'PASS: .scripts/tests/test-gx-maybe.sh')"
check "characterization PASS line" "$(has_line "$out" 'PASS: claude-code-shared/tests/characterization/test-adapter-maybe.sh')"
check "bash scripts/tests PASS line" "$(has_line "$out" 'PASS: claude-code-shared/scripts/tests/test-shared-maybe.sh')"
check "pytest scripts/tests PASS line" "$(has_line "$out" 'PASS: claude-code-shared/scripts/tests/test_py_maybe.py')"
check "no FAIL lines" "$(grep -q '^FAIL:' <<<"$out" && echo 0 || echo 1)"
check "summary 9 passed, 0 failed" "$(has_line "$out" 'Results: 9 passed, 0 failed')"

echo "== failing tree =="
build_tree "$TMP/fail" fail
out="$(bash "$RUNNER" "$TMP/fail" 2>&1)"; rc=$?
check "non-zero exit when any file fails" "$([[ $rc -ne 0 ]] && echo 1 || echo 0)"
check "passing bash file still PASS" "$(has_line "$out" 'PASS: claude-code-shared/scripts/tests/test-shared-ok.sh')"
check "passing pytest file still PASS" "$(has_line "$out" 'PASS: claude-code-shared/scripts/tests/test_py_ok.py')"
check "failing bash (scripts/tests) FAIL line" "$(has_line "$out" 'FAIL: claude-code-shared/scripts/tests/test-shared-maybe.sh')"
check "failing pytest FAIL line" "$(has_line "$out" 'FAIL: claude-code-shared/scripts/tests/test_py_maybe.py')"
check "failing gx FAIL line" "$(has_line "$out" 'FAIL: .scripts/tests/test-gx-maybe.sh')"
check "failing characterization FAIL line" "$(has_line "$out" 'FAIL: claude-code-shared/tests/characterization/test-adapter-maybe.sh')"
check "summary 5 passed, 4 failed" "$(has_line "$out" 'Results: 5 passed, 4 failed')"

echo "== single failure (pytest only) =="
build_tree "$TMP/one" pass
write_py "$TMP/one/claude-code-shared/scripts/tests/test_py_maybe.py" "False"
out="$(bash "$RUNNER" "$TMP/one" 2>&1)"; rc=$?
check "one pytest failure fails the run" "$([[ $rc -ne 0 ]] && echo 1 || echo 0)"
check "summary 8 passed, 1 failed" "$(has_line "$out" 'Results: 8 passed, 1 failed')"

echo "== single failure (bash only) =="
build_tree "$TMP/oneb" pass
write_bash "$TMP/oneb/claude-code-shared/scripts/tests/test-shared-maybe.sh" 3
out="$(bash "$RUNNER" "$TMP/oneb" 2>&1)"; rc=$?
check "one bash failure fails the run" "$([[ $rc -ne 0 ]] && echo 1 || echo 0)"
check "summary 8 passed, 1 failed (bash)" "$(has_line "$out" 'Results: 8 passed, 1 failed')"

# --- Characterization of exact stdout (guards the default output the JSON option must not change) ---
echo "== exact stdout characterization =="
mkdir -p "$TMP/exact/.scripts/tests" "$TMP/exact/claude-code-shared/scripts/tests"
write_bash "$TMP/exact/.scripts/tests/test-a-ok.sh" 0
write_bash "$TMP/exact/.scripts/tests/test-b-bad.sh" 1
write_bash "$TMP/exact/claude-code-shared/scripts/tests/test-c-ok.sh" 0
expected_exact="PASS: .scripts/tests/test-a-ok.sh
FAIL: .scripts/tests/test-b-bad.sh
PASS: claude-code-shared/scripts/tests/test-c-ok.sh

Results: 2 passed, 1 failed
Failed: .scripts/tests/test-b-bad.sh"
out="$(bash "$RUNNER" "$TMP/exact" 2>/dev/null)"; rc=$?
check "failing tree stdout is exactly the PASS/FAIL lines, summary, Failed list" "$([[ "$out" == "$expected_exact" ]] && echo 1 || echo 0)"
check "failing tree exits 1" "$([[ $rc -eq 1 ]] && echo 1 || echo 0)"
expected_pass="PASS: .scripts/tests/test-a-ok.sh
PASS: claude-code-shared/scripts/tests/test-c-ok.sh

Results: 2 passed, 0 failed"
mkdir -p "$TMP/exactpass/.scripts/tests" "$TMP/exactpass/claude-code-shared/scripts/tests"
write_bash "$TMP/exactpass/.scripts/tests/test-a-ok.sh" 0
write_bash "$TMP/exactpass/claude-code-shared/scripts/tests/test-c-ok.sh" 0
out="$(bash "$RUNNER" "$TMP/exactpass" 2>/dev/null)"; rc=$?
check "passing tree stdout is exactly the PASS lines and summary" "$([[ "$out" == "$expected_pass" ]] && echo 1 || echo 0)"
check "passing tree exits 0" "$([[ $rc -eq 0 ]] && echo 1 || echo 0)"

# --- RUN_ALL_TESTS_JSON ---
echo "== RUN_ALL_TESTS_JSON: results file =="
J="$TMP/results.json"
out_json="$(RUN_ALL_TESTS_JSON="$J" bash "$RUNNER" "$TMP/exact" 2>/dev/null)"; rc_json=$?
expected_json='{
  "results": {
    ".scripts/tests/test-a-ok.sh": "pass",
    ".scripts/tests/test-b-bad.sh": "fail",
    "claude-code-shared/scripts/tests/test-c-ok.sh": "pass"
  },
  "totals": {"passed": 2, "failed": 1, "total": 3}
}'
check "JSON file exists" "$([[ -f "$J" ]] && echo 1 || echo 0)"
check "JSON lists every test file with pass/fail and correct totals" "$([[ "$(cat "$J" 2>/dev/null)" == "$expected_json" ]] && echo 1 || echo 0)"
valid="$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(1 if d["totals"]=={"passed":2,"failed":1,"total":3} and d["results"][".scripts/tests/test-b-bad.sh"]=="fail" else 0)' "$J" 2>/dev/null || echo 0)"
check "JSON parses as valid JSON and matches" "$valid"
check "stdout identical with RUN_ALL_TESTS_JSON set (failing tree)" "$([[ "$out_json" == "$expected_exact" ]] && echo 1 || echo 0)"
check "exit code unchanged with JSON set (failing tree is 1)" "$([[ $rc_json -eq 1 ]] && echo 1 || echo 0)"

echo "== RUN_ALL_TESTS_JSON: all-pass tree =="
J2="$TMP/results-pass.json"
out_json="$(RUN_ALL_TESTS_JSON="$J2" bash "$RUNNER" "$TMP/exactpass" 2>/dev/null)"; rc_json=$?
check "stdout identical with JSON set (passing tree)" "$([[ "$out_json" == "$expected_pass" ]] && echo 1 || echo 0)"
check "exit code 0 with JSON set (passing tree)" "$([[ $rc_json -eq 0 ]] && echo 1 || echo 0)"
check "all-pass JSON totals" "$(grep -qF '"totals": {"passed": 2, "failed": 0, "total": 2}' "$J2" && echo 1 || echo 0)"

echo "== RUN_ALL_TESTS_JSON: unset leaves no file =="
mkdir -p "$TMP/nojson"
( cd "$TMP/nojson" && env -u RUN_ALL_TESTS_JSON bash "$RUNNER" "$TMP/exact" >/dev/null 2>&1 )
check "no json file written when unset" "$([[ -z "$(ls -A "$TMP/nojson")" ]] && echo 1 || echo 0)"

echo "== RUN_ALL_TESTS_JSON: unwritable path never changes exit code =="
out_bad="$(RUN_ALL_TESTS_JSON="$TMP/no-such-dir/x/out.json" bash "$RUNNER" "$TMP/exact" 2>/dev/null)"; rc_bad=$?
check "stdout unchanged when JSON path unwritable" "$([[ "$out_bad" == "$expected_exact" ]] && echo 1 || echo 0)"
check "exit code unchanged when JSON path unwritable (failing tree)" "$([[ $rc_bad -eq 1 ]] && echo 1 || echo 0)"
RUN_ALL_TESTS_JSON="$TMP/no-such-dir/x/out.json" bash "$RUNNER" "$TMP/exactpass" >/dev/null 2>&1; rc_bad=$?
check "exit code unchanged when JSON path unwritable (passing tree)" "$([[ $rc_bad -eq 0 ]] && echo 1 || echo 0)"

echo "== RUN_ALL_TESTS_JSON: empty tree =="
mkdir -p "$TMP/empty"
RUN_ALL_TESTS_JSON="$TMP/empty.json" bash "$RUNNER" "$TMP/empty" >/dev/null 2>&1; rc=$?
check "empty tree exits 0" "$([[ $rc -eq 0 ]] && echo 1 || echo 0)"
check "empty tree JSON has zero totals" "$(grep -qF '"totals": {"passed": 0, "failed": 0, "total": 0}' "$TMP/empty.json" && echo 1 || echo 0)"

echo
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
