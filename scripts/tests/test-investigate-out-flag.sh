#!/usr/bin/env bash
# Validate that /investigate SKILL.md contains --out flag parsing and routing logic.
# Exit 0 on all checks pass, 1 on any failure.

SKILL_MD="$(dirname "$0")/../../skills/investigate/SKILL.md"

if [[ ! -f "$SKILL_MD" ]]; then
  echo "FAIL: SKILL.md not found at $SKILL_MD"
  exit 1
fi

PASS=0
FAIL=0

check() {
  local description="$1"
  local pattern="$2"
  if grep -qF -- "$pattern" "$SKILL_MD"; then
    echo "PASS: $description"
    ((PASS++))
  else
    echo "FAIL: $description"
    echo "      (expected to find: $pattern)"
    ((FAIL++))
  fi
}

# Check 1: --out flag parsing step exists
check "--out flag parsing step present" "Parse \`--out\` from the user's args"

# Check 2: Accepted values documented (raw and slack)
check "accepted values raw and slack documented" "Accepted values"

# Check 3: Default is slack
check "default is slack" "Default"

# Check 4: --out raw documented
check "--out raw documented" "--out raw"

# Check 5: --out slack documented
check "--out slack documented" "--out slack"

# Check 6: Strip --out from args before passing to investigator
check "strip --out from args instruction present" "Strip \`--out <value>\`"

# Check 7: Routing step exists
check "routing step (Step 3) present" "Route output based on --out flag"

# Check 8: raw mode returns JSON directly
check "raw mode returns investigation-result JSON directly" "Return the \`investigation-result\` JSON directly"

# Check 9: slack mode routes through answer-composer
check "slack mode routes through answer-composer" "answer-composer"

# Check 10: Invalid value handling
check "invalid --out value error instruction present" "stop and tell the user the valid options"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
