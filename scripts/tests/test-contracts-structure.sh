#!/usr/bin/env bash
# Verifies contracts/ directory structure and schema invariants for T-0001.
set -euo pipefail

CONTRACTS_DIR="$(cd "$(dirname "$0")/../.." && pwd)/contracts"
PASS=0
FAIL=0

assert_pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

check() {
  local desc="$1"
  local result="$2"
  if [ "$result" = "true" ]; then
    assert_pass "$desc"
  else
    assert_fail "$desc"
  fi
}

echo "=== contracts/ structure test ==="

# AC1: Six files exist
for f in task-schema.json task-contract.md seed-schema.json seed-contract.md runner-result-schema.json runner-result-contract.md; do
  check "contracts/$f exists" "$([ -f "$CONTRACTS_DIR/$f" ] && echo true || echo false)"
done

# AC2: All three schemas have additionalProperties: false
for s in task-schema.json seed-schema.json runner-result-schema.json; do
  val=$(python3 -c "import json; d=json.load(open('$CONTRACTS_DIR/$s')); print(str(d.get('additionalProperties') == False).lower())")
  check "$s has additionalProperties: false" "$val"
done

# AC3: schema_version consts
task_ver=$(python3 -c "import json; d=json.load(open('$CONTRACTS_DIR/task-schema.json')); print(d['properties']['schema_version'].get('const',''))")
check "task-schema.json schema_version const is '1'" "$([ "$task_ver" = "1" ] && echo true || echo false)"

seed_ver=$(python3 -c "import json; d=json.load(open('$CONTRACTS_DIR/seed-schema.json')); print(d['properties']['schema_version'].get('const',''))")
check "seed-schema.json schema_version const is '2'" "$([ "$seed_ver" = "2" ] && echo true || echo false)"

runner_ver=$(python3 -c "import json; d=json.load(open('$CONTRACTS_DIR/runner-result-schema.json')); print(d['properties']['schema_version'].get('const',''))")
check "runner-result-schema.json schema_version const is '1'" "$([ "$runner_ver" = "1" ] && echo true || echo false)"

# AC4: task-schema.json has no source_branch; has linear_url
has_source_branch=$(python3 -c "import json; d=json.load(open('$CONTRACTS_DIR/task-schema.json')); items=d['properties']['tasks']['items']['properties']; print('true' if 'source_branch' in items else 'false')")
check "task-schema.json does NOT have source_branch in task items" "$([ "$has_source_branch" = "false" ] && echo true || echo false)"

has_linear_url=$(python3 -c "import json; d=json.load(open('$CONTRACTS_DIR/task-schema.json')); items=d['properties']['tasks']['items']['properties']; print('true' if 'linear_url' in items else 'false')")
check "task-schema.json has linear_url in task items" "$has_linear_url"

# AC5: Each contract.md names producers, consumers, and links to schema file
for md in task-contract.md seed-contract.md runner-result-contract.md; do
  content=$(cat "$CONTRACTS_DIR/$md")
  has_producers=$(echo "$content" | grep -qi "producers" && echo true || echo false)
  has_consumers=$(echo "$content" | grep -qi "consumers" && echo true || echo false)
  has_schema_link=$(echo "$content" | grep -qi "schema.json" && echo true || echo false)
  check "$md mentions Producers" "$has_producers"
  check "$md mentions Consumers" "$has_consumers"
  check "$md links to schema file" "$has_schema_link"
done

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
