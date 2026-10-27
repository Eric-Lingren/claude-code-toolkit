#!/usr/bin/env bash
# Tests that all docs/-path consumers reference resolve-ref.sh.
# Skills are AI-model instructions (SKILL.md); tests verify spec contains wiring.
set -euo pipefail

SKILLS_DIR="$(cd "$(dirname "$0")/../../skills" && pwd)"
AGENTS_DIR="$(cd "$(dirname "$0")/../../agents" && pwd)"
RESOURCES_DIR="$(cd "$(dirname "$0")/../../resources" && pwd)"

PASS=0
FAIL=0

assert_pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

echo "=== resolve-ref.sh wiring in consumer skills ==="

# Helper: check that a file contains resolve-ref.sh reference
check_wired() {
  local label="$1"
  local file="$2"
  if grep -q "resolve-ref.sh" "$file"; then
    assert_pass "$label: contains resolve-ref.sh reference"
  else
    assert_fail "$label: missing resolve-ref.sh reference"
  fi
}

# Helper: check resolve-ref.sh appears before a given pattern
check_before() {
  local label="$1"
  local file="$2"
  local after_pattern="$3"
  local ref_line after_line
  ref_line=$(grep -n "resolve-ref.sh" "$file" | head -1 | cut -d: -f1)
  after_line=$(grep -n "$after_pattern" "$file" | head -1 | cut -d: -f1)
  if [ -n "$ref_line" ] && [ -n "$after_line" ] && [ "$ref_line" -lt "$after_line" ]; then
    assert_pass "$label: resolve-ref.sh appears before '$after_pattern'"
  elif [ -z "$ref_line" ]; then
    assert_fail "$label: resolve-ref.sh not found in file"
  elif [ -z "$after_line" ]; then
    assert_fail "$label: pattern '$after_pattern' not found in file"
  else
    assert_fail "$label: resolve-ref.sh (line $ref_line) appears AFTER '$after_pattern' (line $after_line)"
  fi
}

# Case 1: grill-me wired with resolve-ref.sh
check_wired "Case 1 grill-me" "$SKILLS_DIR/grill-me/SKILL.md"
check_before "Case 1 grill-me" "$SKILLS_DIR/grill-me/SKILL.md" "open_threads"

# Case 2: grill-with-docs wired
check_wired "Case 2 grill-with-docs" "$SKILLS_DIR/grill-with-docs/SKILL.md"
check_before "Case 2 grill-with-docs" "$SKILLS_DIR/grill-with-docs/SKILL.md" "open_threads"

# Case 3: to-seed wired (Mode 2 handoff path)
check_wired "Case 3 to-seed" "$SKILLS_DIR/to-seed/SKILL.md"
check_before "Case 3 to-seed" "$SKILLS_DIR/to-seed/SKILL.md" "Seed Context"

# Case 4: to-tasks wired before process-section extract-prd-json.sh call
# "on the selected file" only appears in the Process step, not the Contract header
check_wired "Case 4 to-tasks" "$SKILLS_DIR/to-tasks/SKILL.md"
check_before "Case 4 to-tasks" "$SKILLS_DIR/to-tasks/SKILL.md" "on the selected file"

# Case 5: to-spec wired before process-section extract-prd-json.sh call
# "to validate and read" only appears in the Process step, not the Contract header
check_wired "Case 5 to-spec" "$SKILLS_DIR/to-spec/SKILL.md"
check_before "Case 5 to-spec" "$SKILLS_DIR/to-spec/SKILL.md" "to validate and read the seed JSON"

# Case 6: build-code wired before "Read the chosen JSON file"
check_wired "Case 6 build-code" "$SKILLS_DIR/build-code/SKILL.md"
check_before "Case 6 build-code" "$SKILLS_DIR/build-code/SKILL.md" "Read the chosen JSON file"

# Case 7: dispatch-tasks wired before "Read the task file"
check_wired "Case 7 dispatch-tasks" "$SKILLS_DIR/dispatch-tasks/SKILL.md"
check_before "Case 7 dispatch-tasks" "$SKILLS_DIR/dispatch-tasks/SKILL.md" "Read the task file"

# Case 8: attribution-tracer wired for tasks_path and seed_path
TRACER="$AGENTS_DIR/attribution-tracer.md"
check_wired "Case 8 attribution-tracer" "$TRACER"
# Check wired in both Layer 2 and Layer 3
if grep -c "resolve-ref.sh" "$TRACER" | grep -q "^2$"; then
  assert_pass "Case 8 attribution-tracer: resolve-ref.sh appears twice (tasks + seed)"
else
  COUNT=$(grep -c "resolve-ref.sh" "$TRACER" || echo 0)
  assert_fail "Case 8 attribution-tracer: expected 2 occurrences, got $COUNT"
fi

# Case 9: bypass behavior mentioned in each wired skill
for skill in grill-me grill-with-docs to-seed to-tasks to-spec build-code dispatch-tasks; do
  SKILL_FILE="$SKILLS_DIR/$skill/SKILL.md"
  if grep -q "bypass\|Continue anyway" "$SKILL_FILE"; then
    assert_pass "Case 9 $skill: bypass behavior mentioned"
  else
    assert_fail "Case 9 $skill: bypass behavior not mentioned"
  fi
done

# Case 10: resolve-ref-pattern.md resource exists with the pattern docs
PATTERN_DOC="$RESOURCES_DIR/resolve-ref-pattern.md"
if [ -f "$PATTERN_DOC" ]; then
  assert_pass "Case 10: resolve-ref-pattern.md exists"
else
  assert_fail "Case 10: resolve-ref-pattern.md missing"
fi
if grep -q "ARCHIVE:" "$PATTERN_DOC" && grep -q "not-found" "$PATTERN_DOC"; then
  assert_pass "Case 10: pattern doc covers archive hit and not-found"
else
  assert_fail "Case 10: pattern doc missing ARCHIVE: or not-found documentation"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
