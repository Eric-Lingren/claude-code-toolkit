#!/usr/bin/env bash
# Smoke test: capture skill, capture-idea agent and hotkey session tier registration.
# Usage: bash test-capture-registration.sh   (exit 0 = all pass)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SHARED="$(cd "$HERE/../.." && pwd)"
PASS=0; FAIL=0
ok() { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check() { if [ "$2" = true ]; then ok "$1"; else bad "$1"; fi; }
py() { python3 -c "$1" "$SHARED" >/dev/null 2>&1 && echo true || echo false; }
LOOKUP='
import json,sys
d=json.load(open(sys.argv[1]+"/resources/model-tiers.json"))
def tier(kind,name): return d["tiers"][d[kind][name]]
'

echo "=== capture registration smoke test ==="
check "tier lookup: skills.capture is sonnet/low" "$(py "$LOOKUP"'
t=tier("skills","capture");assert (t["model"],t["effort"])==("sonnet","low")')"
check "tier lookup: agents.capture-idea is sonnet" "$(py "$LOOKUP"'assert tier("agents","capture-idea")["model"]=="sonnet"')"
check "tier lookup: sessions.capture-hotkey is haiku/low" "$(py "$LOOKUP"'
t=tier("sessions","capture-hotkey");assert (t["model"],t["effort"])==("haiku","low")')"
check "tier lookup: export-tasks-github stays haiku" "$(py "$LOOKUP"'assert tier("agents","export-tasks-github")["model"]=="haiku"')"
check "hotkey launcher flags match sessions.capture-hotkey tier" "$(grep -q -- '--model haiku --effort low' "$SHARED/scripts/capture/capture" && echo true || echo false)"
check "SKILL.md frontmatter matches capture tier" "$(grep -q '^model: sonnet$' "$SHARED/skills/capture/SKILL.md" && grep -q '^effort: low$' "$SHARED/skills/capture/SKILL.md" && echo true || echo false)"
check "capture-idea frontmatter model matches" "$(grep -q '^model: sonnet$' "$SHARED/agents/learning/capture-idea.md" && echo true || echo false)"
check "registry.json: capture-idea entry with existing file" "$(py '
import json,os,sys
r=sys.argv[1]
reg=json.load(open(r+"/agents/registry.json"))
reg=reg["agents"] if isinstance(reg,dict) else reg
e=[x for x in reg if x["name"]=="capture-idea"][0]
assert os.path.isfile(r+"/"+e["file"]) and e["model"]=="sonnet"')"
check "SKILL.md has Contract section with validate-schema.sh" "$(grep -q '^## Contract' "$SHARED/skills/capture/SKILL.md" && grep -q 'validate-schema.sh' "$SHARED/skills/capture/SKILL.md" && echo true || echo false)"
check "learning-contract lists skills/capture/" "$(grep -q '^- `skills/capture/`' "$SHARED/contracts/learning-contract.md" && echo true || echo false)"
check "benchmark CMD set includes capture" "$(grep -q '"pick", "capture"' "$SHARED/scripts/usage/cc-usage-benchmark.py" && echo true || echo false)"
check "sync-model-tiers --check clean (worktree config)" "$(python3 "$SHARED/scripts/registration/sync-model-tiers.py" --check --config "$SHARED/resources/model-tiers.json" --skills-dir "$SHARED/skills" --agents-dir "$SHARED/agents" 2>&1 | grep -q 'would change: 0' && echo true || echo false)"

echo "=== $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
