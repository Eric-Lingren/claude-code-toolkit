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

echo
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
