#!/usr/bin/env bash
# test_investigator_bounded_expansion.sh
# Validates that investigator.md implements bounded expansion per T-0098.
#
# Exit 0 = all checks pass
# Exit 1 = one or more checks failed

set -euo pipefail

SHARED="$(cd "$(dirname "$0")/../.." && pwd)"
AGENT="$SHARED/agents/investigate/investigator.md"
SCHEMA="$SHARED/contracts/investigation-result-schema.json"
CONTRACT="$SHARED/contracts/investigation-result-contract.md"

PASS=0
FAIL=0

check() {
  local desc="$1"
  local result="$2"  # "pass" or "fail"
  if [ "$result" = "pass" ]; then
    echo "PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $desc"
    FAIL=$((FAIL + 1))
  fi
}

agent_text=$(cat "$AGENT")
schema_text=$(cat "$SCHEMA")
contract_text=$(cat "$CONTRACT")

# ---------------------------------------------------------------------------
# 1. Bounded expansion instructions exist in investigator.md
# ---------------------------------------------------------------------------

if echo "$agent_text" | grep -qi "bounded expansion"; then
  check "bounded expansion instruction present in investigator.md" pass
else
  check "bounded expansion instruction present in investigator.md" fail
fi

if echo "$agent_text" | grep -qi "adjacent.fact\|adjacent_fact"; then
  check "adjacent_facts field documented in investigator.md" pass
else
  check "adjacent_facts field documented in investigator.md" fail
fi

if echo "$agent_text" | grep -qi "relevance.gat\|relevance-gate"; then
  check "relevance-gate documented in investigator.md" pass
else
  check "relevance-gate documented in investigator.md" fail
fi

if echo "$agent_text" | grep -qi "2.4\|2–4\|cap"; then
  check "cap on adjacent facts documented in investigator.md" pass
else
  check "cap on adjacent facts documented in investigator.md" fail
fi

# ---------------------------------------------------------------------------
# 2. Blob-URL citation requirement is stated
# ---------------------------------------------------------------------------

if echo "$agent_text" | grep -q "github.com.*blob.*#L"; then
  check "blob-URL citation pattern present in investigator.md" pass
else
  check "blob-URL citation pattern present in investigator.md" fail
fi

if echo "$agent_text" | grep -qi "git remote get-url\|git rev-parse"; then
  check "blob-URL construction commands referenced in investigator.md" pass
else
  check "blob-URL construction commands referenced in investigator.md" fail
fi

if echo "$agent_text" | grep -qi "never use.*file:line\|not.*bare.*file:line\|bare.*file:line"; then
  check "investigator.md forbids bare file:line refs in adjacent facts" pass
else
  check "investigator.md forbids bare file:line refs in adjacent facts" fail
fi

# ---------------------------------------------------------------------------
# 3. Relevance-gate / drop-if-uninformative is documented
# ---------------------------------------------------------------------------

if echo "$agent_text" | grep -qi "drop\|silently drop\|excluded\|evidence gate"; then
  check "evidence gate (drop uninformative facts) documented in investigator.md" pass
else
  check "evidence gate (drop uninformative facts) documented in investigator.md" fail
fi

if echo "$agent_text" | grep -qi "INSUFFICIENT_EVIDENCE.*drop\|drop.*INSUFFICIENT_EVIDENCE\|returning.*INSUFFICIENT.*silently\|silently dropped"; then
  check "INSUFFICIENT_EVIDENCE adjacent facts are silently dropped" pass
else
  check "INSUFFICIENT_EVIDENCE adjacent facts are silently dropped" fail
fi

# ---------------------------------------------------------------------------
# 4. investigation-result schema includes adjacent_facts field
# ---------------------------------------------------------------------------

if echo "$schema_text" | grep -q '"adjacent_facts"'; then
  check "investigation-result-schema.json has adjacent_facts field" pass
else
  check "investigation-result-schema.json has adjacent_facts field" fail
fi

if echo "$schema_text" | python3 -c "import sys,json; schema=json.load(sys.stdin); props=schema.get('properties',{}); af=props.get('adjacent_facts',{}); exit(0 if af.get('type')=='array' else 1)"; then
  check "adjacent_facts is typed as array in schema" pass
else
  check "adjacent_facts is typed as array in schema" fail
fi

# Verify schema is still valid JSON
if echo "$schema_text" | python3 -m json.tool > /dev/null 2>&1; then
  check "investigation-result-schema.json is valid JSON" pass
else
  check "investigation-result-schema.json is valid JSON" fail
fi

# Verify adjacents_facts items have required fields claim, verdict, evidence
if echo "$schema_text" | python3 -c "
import sys, json
schema = json.load(sys.stdin)
af = schema['properties']['adjacent_facts']
items = af.get('items', {})
required = items.get('required', [])
for field in ['claim', 'verdict', 'evidence']:
    if field not in required:
        print(f'Missing required field: {field}')
        sys.exit(1)
"; then
  check "adjacent_facts items require claim, verdict, evidence" pass
else
  check "adjacent_facts items require claim, verdict, evidence" fail
fi

# ---------------------------------------------------------------------------
# 5. Schema accommodates a representative expansion result (validation test)
# ---------------------------------------------------------------------------

SAMPLE=$(cat <<'EOF'
{
  "schema_version": "1",
  "verdict": "VERIFIED_TRUE",
  "evidence": [
    {
      "source": "code",
      "ref": "https://github.com/org/repo/blob/main/src/auth/middleware.ts#L88",
      "quote": "export const HOOK_ENABLED = true;"
    }
  ],
  "summary": "The hook is enabled at middleware.ts line 88.",
  "adjacent_facts": [
    {
      "claim": "Tests cover the hook enablement path.",
      "verdict": "VERIFIED_TRUE",
      "evidence": [
        {
          "source": "code",
          "ref": "https://github.com/org/repo/blob/main/src/auth/middleware.test.ts#L22",
          "quote": "it('enables hook when HOOK_ENABLED is true', ...)"
        }
      ],
      "summary": "A test asserts the enabled path."
    }
  ]
}
EOF
)

if echo "$SAMPLE" | python3 -c "
import sys, json, jsonschema, pathlib
instance = json.load(sys.stdin)
schema = json.loads(pathlib.Path('$SCHEMA').read_text())
try:
    jsonschema.validate(instance, schema)
    print('schema valid')
except jsonschema.ValidationError as e:
    print(f'INVALID: {e.message}')
    sys.exit(1)
" 2>/dev/null; then
  check "representative expansion result validates against schema" pass
else
  check "representative expansion result validates against schema" fail
fi

# Verify a result WITHOUT adjacent_facts still validates (backward compat)
SAMPLE_NO_AF=$(cat <<'EOF'
{
  "schema_version": "1",
  "verdict": "INSUFFICIENT_EVIDENCE",
  "evidence": [],
  "summary": "No evidence found."
}
EOF
)

if echo "$SAMPLE_NO_AF" | python3 -c "
import sys, json, jsonschema, pathlib
instance = json.load(sys.stdin)
schema = json.loads(pathlib.Path('$SCHEMA').read_text())
try:
    jsonschema.validate(instance, schema)
    print('schema valid')
except jsonschema.ValidationError as e:
    print(f'INVALID: {e.message}')
    sys.exit(1)
" 2>/dev/null; then
  check "result without adjacent_facts still validates (backward compat)" pass
else
  check "result without adjacent_facts still validates (backward compat)" fail
fi

# ---------------------------------------------------------------------------
# 6. Contract doc updated
# ---------------------------------------------------------------------------

if echo "$contract_text" | grep -qi "adjacent_fact\|adjacent fact"; then
  check "investigation-result-contract.md documents adjacent_facts" pass
else
  check "investigation-result-contract.md documents adjacent_facts" fail
fi

if echo "$contract_text" | grep -qi "evidence gate\|drop.*uninformative\|excluded"; then
  check "contract documents evidence gate rule" pass
else
  check "contract documents evidence gate rule" fail
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi

exit 0
