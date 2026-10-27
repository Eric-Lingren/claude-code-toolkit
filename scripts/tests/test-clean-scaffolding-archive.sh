#!/usr/bin/env bash
# Tests for clean-scaffolding.sh archive-files mode and disposition logic.
set -euo pipefail

SKILL_SCRIPT="$(cd "$(dirname "$0")/../../skills/clean-scaffolding/scripts" && pwd)/clean-scaffolding.sh"

TMP=$(mktemp -d)
PASS=0
FAIL=0

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

assert_pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

# Each case runs in its own subdirectory so docs/ structure is isolated
new_case_dir() { local d="$TMP/case_$1"; mkdir -p "$d"; echo "$d"; }

echo "=== clean-scaffolding.sh archive-files tests ==="

# Case 1: Bundle seed + task into docs/archive/, originals removed
CASE=$(new_case_dir 1)
mkdir -p "$CASE/docs/seeds" "$CASE/docs/tasks" "$CASE/docs/archive"
SEED="$CASE/docs/seeds/20260101-0900-my-feature.json"
TASK="$CASE/docs/tasks/20260101-0910-my-feature.json"
cat > "$SEED" <<'EOF'
{"schema_version":"4","title":"My Feature","slug":"my-feature","status":"ready"}
EOF
cat > "$TASK" <<'EOF'
{"schema_version":"2","title":"My Feature Tasks"}
EOF

(cd "$CASE" && bash "$SKILL_SCRIPT" archive-files docs/seeds/20260101-0900-my-feature.json docs/tasks/20260101-0910-my-feature.json) > "$TMP/out1" 2>&1

BUNDLE=$(ls "$CASE/docs/archive/" 2>/dev/null | head -1)
if [ -n "$BUNDLE" ]; then
  assert_pass "Case 1: archive bundle written to docs/archive/"
else
  assert_fail "Case 1: no bundle written. Output: $(cat "$TMP/out1")"
fi
if [ ! -f "$SEED" ] && [ ! -f "$TASK" ]; then
  assert_pass "Case 1: originals removed after archiving"
else
  assert_fail "Case 1: originals not removed. SEED exists: $([ -f "$SEED" ] && echo yes || echo no), TASK exists: $([ -f "$TASK" ] && echo yes || echo no)"
fi
if echo "$BUNDLE" | grep -q "20260101-0900-my-feature"; then
  assert_pass "Case 1: archive filename uses root-timestamp and slug"
else
  assert_fail "Case 1: archive filename wrong. Got: $BUNDLE"
fi

# Case 2: Bundle schema has required fields (kind, producer, slug, title, condensed_from, artifacts)
BUNDLE_PATH="$CASE/docs/archive/$BUNDLE"
BUNDLE_DATA=$(python3 -c "import json; d=json.load(open('$BUNDLE_PATH')); print(json.dumps(d))" 2>/dev/null || echo "INVALID_JSON")
if echo "$BUNDLE_DATA" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['kind']=='condensed'" 2>/dev/null; then
  assert_pass "Case 2: bundle kind == 'condensed'"
else
  assert_fail "Case 2: bundle kind missing or wrong. Data: $BUNDLE_DATA"
fi
if echo "$BUNDLE_DATA" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['producer']=='clean-scaffolding'" 2>/dev/null; then
  assert_pass "Case 2: bundle producer == 'clean-scaffolding'"
else
  assert_fail "Case 2: bundle producer missing or wrong"
fi
if echo "$BUNDLE_DATA" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'condensed_from' in d and len(d['condensed_from'])==2" 2>/dev/null; then
  assert_pass "Case 2: condensed_from lists 2 basenames"
else
  assert_fail "Case 2: condensed_from wrong. Data: $BUNDLE_DATA"
fi
if echo "$BUNDLE_DATA" | python3 -c "import json,sys; d=json.load(sys.stdin); assert len(d['artifacts'])==2" 2>/dev/null; then
  assert_pass "Case 2: artifacts[] has 2 entries"
else
  assert_fail "Case 2: artifacts wrong count"
fi
if echo "$BUNDLE_DATA" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['title']=='My Feature'" 2>/dev/null; then
  assert_pass "Case 2: bundle title extracted from root JSON"
else
  assert_fail "Case 2: bundle title wrong. Data: $BUNDLE_DATA"
fi

# Case 3: Seed artifact content is verbatim JSON
if echo "$BUNDLE_DATA" | python3 -c "
import json, sys
d = json.load(sys.stdin)
seed_artifact = next(a for a in d['artifacts'] if a['filename'].startswith('20260101-0900'))
assert seed_artifact['content']['schema_version'] == '4'
assert seed_artifact['type'] == 'seed'
" 2>/dev/null; then
  assert_pass "Case 3: seed artifact has verbatim JSON content and type 'seed'"
else
  assert_fail "Case 3: seed artifact content/type wrong. Data: $BUNDLE_DATA"
fi

# Case 4: Task artifact type is 'task'
if echo "$BUNDLE_DATA" | python3 -c "
import json, sys
d = json.load(sys.stdin)
task_artifact = next(a for a in d['artifacts'] if a['filename'].startswith('20260101-0910'))
assert task_artifact['type'] == 'task'
" 2>/dev/null; then
  assert_pass "Case 4: task artifact has type 'task'"
else
  assert_fail "Case 4: task artifact type wrong. Data: $BUNDLE_DATA"
fi

# Case 5: Handoff .md content stored as string
CASE5=$(new_case_dir 5)
mkdir -p "$CASE5/docs/seeds" "$CASE5/docs/handoffs" "$CASE5/docs/archive"
SEED5="$CASE5/docs/seeds/20260101-1000-handoff-test.json"
HANDOFF5="$CASE5/docs/handoffs/20260101-1010-handoff-test.md"
echo '{"schema_version":"4","title":"Handoff test","slug":"handoff-test"}' > "$SEED5"
printf '# Handoff: handoff test\n\n**Source ref:** 20260101-1000-handoff-test.json\n\nBody text.\n' > "$HANDOFF5"

(cd "$CASE5" && bash "$SKILL_SCRIPT" archive-files docs/seeds/20260101-1000-handoff-test.json docs/handoffs/20260101-1010-handoff-test.md) > "$TMP/out5" 2>&1
BUNDLE5=$(ls "$CASE5/docs/archive/" 2>/dev/null | head -1)
if [ -n "$BUNDLE5" ]; then
  set +e
  python3 - "$CASE5/docs/archive/$BUNDLE5" > "$TMP/py5out" 2>&1 <<'PYEOF'
import json, sys
bundle_path = sys.argv[1]
with open(bundle_path) as f:
    d = json.load(f)
handoff_a = next(a for a in d['artifacts'] if a['type'] == 'handoff')
assert isinstance(handoff_a['content'], str), f'expected str, got {type(handoff_a["content"])}'
assert handoff_a['type'] == 'handoff', f'expected handoff, got {handoff_a["type"]}'
print("OK")
PYEOF
  PYEXIT=$?
  set -e
  if [ "$PYEXIT" -eq 0 ]; then
    assert_pass "Case 5: handoff .md stored as string with type 'handoff'"
  else
    assert_fail "Case 5: handoff content not string or type wrong. Error: $(cat "$TMP/py5out")"
  fi
else
  assert_fail "Case 5: no bundle written for handoff chain. Output: $(cat "$TMP/out5")"
fi

# Case 6: browser-checks deleted via delete-files (not archived)
CASE6=$(new_case_dir 6)
mkdir -p "$CASE6/docs/browser-checks"
BC_FILE="$CASE6/docs/browser-checks/check-20260101.json"
echo '{"result":"pass"}' > "$BC_FILE"
(cd "$CASE6" && bash "$SKILL_SCRIPT" delete-files docs/browser-checks/check-20260101.json) > "$TMP/out6" 2>&1
if [ ! -f "$BC_FILE" ]; then
  assert_pass "Case 6: browser-check file deleted by delete-files"
else
  assert_fail "Case 6: browser-check file not deleted"
fi
if [ ! -d "$CASE6/docs/archive" ] || [ -z "$(ls "$CASE6/docs/archive/" 2>/dev/null)" ]; then
  assert_pass "Case 6: no archive bundle created for browser-checks"
else
  assert_fail "Case 6: archive bundle wrongly created for browser-checks"
fi

# Case 6b: docs/tasks/.logs trace files deleted via delete-files (not archived), leaving docs/archive untouched
CASE6B=$(new_case_dir 6b)
mkdir -p "$CASE6B/docs/tasks/.logs/my-taskfile" "$CASE6B/docs/archive"
echo '{"kept":"marker"}' > "$CASE6B/docs/archive/keep-me.json"
LOG_FILE="$CASE6B/docs/tasks/.logs/my-taskfile/T-0001.md"
echo '# trace' > "$LOG_FILE"
(cd "$CASE6B" && bash "$SKILL_SCRIPT" delete-files docs/tasks/.logs/my-taskfile/T-0001.md) > "$TMP/out6b" 2>&1
if [ ! -f "$LOG_FILE" ]; then
  assert_pass "Case 6b: .logs trace file deleted by delete-files"
else
  assert_fail "Case 6b: .logs trace file not deleted"
fi
if [ -f "$CASE6B/docs/archive/keep-me.json" ]; then
  assert_pass "Case 6b: docs/archive contents left intact"
else
  assert_fail "Case 6b: docs/archive contents were wrongly removed"
fi

# Case 6c: preview enumerates docs/tasks/.logs alongside browser-checks as delete set
CASE6C=$(new_case_dir 6c)
mkdir -p "$CASE6C/docs/tasks/.logs/my-taskfile"
echo '# trace' > "$CASE6C/docs/tasks/.logs/my-taskfile/T-0002.md"
PREVIEW_OUT=$(cd "$CASE6C" && bash "$SKILL_SCRIPT" preview 2>&1 || true)
if echo "$PREVIEW_OUT" | grep -q "T-0002.md"; then
  assert_pass "Case 6c: preview lists docs/tasks/.logs trace files"
else
  assert_fail "Case 6c: preview did not list .logs trace file. Output: $PREVIEW_OUT"
fi

# Case 7: archive-files requires at least one file argument
set +e
ERR_OUT=$(bash "$SKILL_SCRIPT" archive-files 2>&1 || true)
EXIT7=$?
set -e
if [ "$EXIT7" -ne 0 ] || echo "$ERR_OUT" | grep -qi "usage\|no files\|at least one"; then
  assert_pass "Case 7: archive-files with no args exits non-zero or shows usage"
else
  assert_fail "Case 7: archive-files with no args did not error. Exit: $EXIT7, out: $ERR_OUT"
fi

# Case 8: SKILL.md uses docs/prototype/ (not docs/prototypes/) — path drift fixed
SKILL_MD="$(cd "$(dirname "$0")/../../skills/clean-scaffolding" && pwd)/SKILL.md"
if grep -q "docs/prototype/" "$SKILL_MD" && ! grep -q "docs/prototypes/" "$SKILL_MD"; then
  assert_pass "Case 8: SKILL.md uses docs/prototype/ (no prototypes/ drift)"
else
  assert_fail "Case 8: SKILL.md has wrong prototype path. Check for docs/prototypes/. Content: $(grep -n prototype "$SKILL_MD")"
fi

# Case 9: SKILL.md confirm copy does not say 'cannot be undone'
if ! grep -q "cannot be undone" "$SKILL_MD"; then
  assert_pass "Case 9: confirm copy does not say 'cannot be undone'"
else
  assert_fail "Case 9: confirm copy still says 'cannot be undone'"
fi

# Case 10: SKILL.md description says 'Archive' not 'Delete'
if head -5 "$SKILL_MD" | grep -q "Archive consumed"; then
  assert_pass "Case 10: SKILL.md description says 'Archive consumed'"
else
  assert_fail "Case 10: SKILL.md description missing 'Archive consumed'. Got: $(head -5 "$SKILL_MD")"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
