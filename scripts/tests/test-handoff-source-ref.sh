#!/usr/bin/env bash
# Tests for source.ref in handoff and prd producers.
# Handoff outputs .md (not JSON); verifies SKILL.md spec and format parseability.
# PRD prd-provenance block already carries source.ref; verifies spec still present.
set -euo pipefail

SKILLS_DIR="$(cd "$(dirname "$0")/../../skills" && pwd)"
HANDOFF_SKILL="$SKILLS_DIR/handoff/SKILL.md"
PRD_SKILL="$SKILLS_DIR/to-spec/SKILL.md"

TMP=$(mktemp -d)
PASS=0
FAIL=0

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

assert_pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

echo "=== source.ref in handoff and prd producers ==="

# --- Handoff skill spec ---

# Case 1: SKILL.md instructs emitting source_ref header line
if grep -q "Source ref" "$HANDOFF_SKILL"; then
  assert_pass "Case 1: handoff/SKILL.md contains 'Source ref' instruction"
else
  assert_fail "Case 1: handoff/SKILL.md missing 'Source ref' instruction"
fi

# Case 2: SKILL.md uses basename-only format (no directory prefix)
if grep -q "basename" "$HANDOFF_SKILL"; then
  assert_pass "Case 2: handoff spec specifies basename-only (no directory prefix)"
else
  assert_fail "Case 2: handoff spec missing basename-only instruction"
fi

# Case 3: source_ref line is grep-extractable from a fixture handoff .md
FIXTURE_MD="$TMP/20260606-1234-test-feature.md"
cat > "$FIXTURE_MD" <<'EOF'
# Handoff: test feature

**Source ref:** `20260606-1200-test-feature-seed.json`
**Branch:** `feat/test-feature`
**Status:** 1 open thread remains.

## What this is

A test handoff fixture.
EOF

EXTRACTED=$(grep -o '`[^`]*\.json`' "$FIXTURE_MD" | head -1 | tr -d '`' || true)
if [ "$EXTRACTED" = "20260606-1200-test-feature-seed.json" ]; then
  assert_pass "Case 3: source_ref basename extractable via grep from fixture .md"
else
  assert_fail "Case 3: could not extract source_ref from fixture .md. Got: '$EXTRACTED'"
fi

# Case 4: extraction works for Source ref line specifically (not other backtick-quoted values)
SOURCE_LINE=$(grep "Source ref" "$FIXTURE_MD" | grep -o '`[^`]*`' | tr -d '`' || true)
if [ "$SOURCE_LINE" = "20260606-1200-test-feature-seed.json" ]; then
  assert_pass "Case 4: source_ref extractable from 'Source ref:' line specifically"
else
  assert_fail "Case 4: source_ref line extraction failed. Got: '$SOURCE_LINE'"
fi

# --- PRD prd-provenance block ---

# Case 5: to-spec SKILL.md still specifies prd-provenance block with source.ref
if grep -q "prd-provenance" "$PRD_SKILL"; then
  assert_pass "Case 5: to-spec/SKILL.md contains prd-provenance block spec"
else
  assert_fail "Case 5: to-spec/SKILL.md missing prd-provenance spec"
fi

# Case 6: prd-provenance spec includes source.ref field
if grep -q '"source"' "$PRD_SKILL" && grep -q '"ref"' "$PRD_SKILL"; then
  assert_pass "Case 6: prd-provenance spec includes source.ref fields"
else
  assert_fail "Case 6: prd-provenance spec missing source.ref fields"
fi

# Case 7: prd-provenance source.ref is extractable from a fixture HTML file
FIXTURE_HTML="$TMP/test-prd.html"
cat > "$FIXTURE_HTML" <<'EOF'
<html><body>
<script type="application/json" id="prd-provenance">
{
  "producer": "to-spec",
  "source": {"type": "seed", "ref": "20260606-1200-test-feature-seed.json"}
}
</script>
</body></html>
EOF

EXTRACTED_REF=$(python3 -c "
import json, re
with open('$FIXTURE_HTML') as f:
    html = f.read()
m = re.search(r'id=\"prd-provenance\">(.*?)</script>', html, re.DOTALL)
if m:
    d = json.loads(m.group(1))
    print(d['source']['ref'])
" 2>/dev/null || true)
if [ "$EXTRACTED_REF" = "20260606-1200-test-feature-seed.json" ]; then
  assert_pass "Case 7: prd-provenance source.ref extractable from HTML fixture"
else
  assert_fail "Case 7: could not extract source.ref from HTML prd-provenance. Got: '$EXTRACTED_REF'"
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
