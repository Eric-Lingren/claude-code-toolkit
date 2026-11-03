#!/usr/bin/env bash
# Tests for scripts/egress/wall-check.sh. Characterizes today's wall rules
# (export-tasks step 4 / dispatch-tasks preview) and the --mode capture variant.
# Usage: bash test-wall-check.sh   (exit 0 = all pass)
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SHARED="$(cd "$HERE/../.." && pwd)"
W="$SHARED/scripts/egress/wall-check.sh"
TMP=$(mktemp -d)
cleanup() { rm -f "$TMP"/*; rmdir "$TMP" 2>/dev/null; }
trap cleanup EXIT
PASS=0; FAIL=0
ok() { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
check() { if [ "$2" = true ]; then ok "$1"; else bad "$1"; fi; }

# run <args...> -> sets RC, OUT (stdout), ERR (stderr)
run() {
  bash "$W" "$@" >"$TMP/out" 2>"$TMP/err"; RC=$?
  OUT=$(cat "$TMP/out"); ERR=$(cat "$TMP/err")
}
rc_is() { [ "$RC" -eq "$1" ] && echo true || echo false; }
silent_pass() { [ "$RC" -eq 0 ] && [ -z "$OUT$ERR" ] && echo true || echo false; }
has_warning() { [ "$RC" -eq 0 ] && printf '%s' "$OUT" | grep -q '^WARNING:' && echo true || echo false; }
err_has() { printf '%s' "$ERR" | grep -q "$1" && echo true || echo false; }
# Corporate domain + repo come from the real (private) policy so no org name is hardcoded.
CORP=$(jq -r '.destinations | to_entries[] | select(.value.wall == "corporate") | .key' "$SHARED/resources/task-routing.json" | head -1)
CORP_REPO=$(jq -r --arg d "$CORP" 'to_entries[] | select(.value.domain? == $d) | .key' "$SHARED/resources/repo-policy.json" | head -1)
[ -n "$CORP" ] && [ -n "$CORP_REPO" ] || { echo "FAIL: no corporate domain/repo in real policy"; exit 1; }

echo "=== wall-check tests ==="

echo "-- default mode, real policy (characterization of today's rules)"
run "$CORP" spawned-sapien
check "corporate -> project: blocked (1)" "$(rc_is 1)"
check "corporate -> project: reason on stderr" "$([ -n "$ERR" ] && echo true || echo false)"
check "corporate -> project: nothing on stdout" "$([ -z "$OUT" ] && echo true || echo false)"
run "$CORP" personal
check "corporate -> personal: blocked (1)" "$(rc_is 1)"
run personal "$CORP"
check "personal -> corporate: blocked (1)" "$(rc_is 1)"
run spawned-sapien "$CORP"
check "project -> corporate: blocked (1)" "$(rc_is 1)"
run "$CORP" "$CORP"
check "corporate -> same corporate: pass" "$(rc_is 0)"
run personal personal
check "personal -> personal: pass, no output" "$(silent_pass)"
run spawned-sapien spawned-sapien
check "spawned-sapien -> spawned-sapien: pass, no output" "$(silent_pass)"
run personal spawned-sapien
check "personal -> project: pass, no rule hit" "$(silent_pass)"
run spawned-sapien personal
check "project -> personal: exit 0 with WARNING on stdout" "$(has_warning)"

echo "-- repo names resolve through repo-policy.json"
run Eric-Lingren/dotfiles Eric-Lingren/SpawnedSapien
check "dotfiles -> SpawnedSapien: pass, no rule hit" "$(silent_pass)"
run Eric-Lingren/SpawnedSapien Eric-Lingren/SpawnedSapien
check "SpawnedSapien -> SpawnedSapien: pass, no rule hit" "$(silent_pass)"
run "$CORP_REPO" Eric-Lingren/SpawnedSapien
check "corp repo -> SpawnedSapien: blocked (default)" "$(rc_is 1)"
check "corp repo -> SpawnedSapien: names corporate-to-anywhere" "$(err_has corporate-to-anywhere)"

echo "-- source from cwd repo (-)"
PICK_REPO="$CORP_REPO" run - Eric-Lingren/SpawnedSapien
check "cwd corp repo (-) -> SpawnedSapien: blocked" "$(rc_is 1)"
PICK_REPO=Eric-Lingren/dotfiles run - Eric-Lingren/SpawnedSapien
check "cwd repo dotfiles (-) -> SpawnedSapien: pass" "$(rc_is 0)"

echo "-- capture mode"
run --mode capture "$CORP" personal
check "capture: corporate -> personal: sealed-required (10)" "$(rc_is 10)"
run --mode capture "$CORP_REPO" Eric-Lingren/SpawnedSapien
check "capture: corp repo -> SpawnedSapien: sealed-required (10)" "$(rc_is 10)"
check "capture: sealed-required names corporate-to-anywhere" "$(err_has corporate-to-anywhere)"
run --mode capture personal "$CORP"
check "capture: non-corporate -> corporate still blocked (1)" "$(rc_is 1)"
run --mode capture spawned-sapien "$CORP"
check "capture: project -> corporate still blocked (1)" "$(rc_is 1)"
run --mode capture "$CORP" "$CORP"
check "capture: corporate -> same corporate: pass" "$(rc_is 0)"
run --mode capture personal spawned-sapien
check "capture: personal -> project: pass" "$(silent_pass)"
run --mode capture spawned-sapien personal
check "capture: project -> personal: exit 0 + WARNING" "$(has_warning)"
run --mode capture personal personal
check "capture: same-domain pass" "$(silent_pass)"

echo "-- two corporate domains never cross (fixture policy)"
cat >"$TMP/policy.json" <<'JSON'
{"o/acme":{"domain":"acme"},"o/globex":{"domain":"globex"},"o/me":{"domain":"personal"}}
JSON
cat >"$TMP/routing.json" <<'JSON'
{"destinations":{"acme":{"wall":"corporate"},"globex":{"wall":"corporate"},"personal":{"wall":"personal"}}}
JSON
export WALL_POLICY="$TMP/policy.json" WALL_ROUTING="$TMP/routing.json"
run o/acme o/globex
check "acme -> globex: blocked (1)" "$(rc_is 1)"
run --mode capture o/acme o/globex
check "capture: acme -> globex: sealed-required (10)" "$(rc_is 10)"
run o/globex o/acme
check "globex -> acme: blocked (1)" "$(rc_is 1)"
run o/me o/acme
check "personal -> acme: blocked (1)" "$(rc_is 1)"
run o/acme o/acme
check "acme -> acme: pass" "$(rc_is 0)"
unset WALL_POLICY WALL_ROUTING

echo "-- usage and config errors fail closed"
run "$CORP"
check "missing dest: exit 2" "$(rc_is 2)"
run nope-domain personal
check "unknown source domain: exit 2" "$(rc_is 2)"
run personal nope-domain
check "unknown dest domain: exit 2" "$(rc_is 2)"
run --mode bogus personal personal
check "bad mode: exit 2" "$(rc_is 2)"
run
check "no args: exit 2" "$(rc_is 2)"

echo "-- documentation wiring"
grep -q 'Exit codes' "$W" 2>/dev/null
check "script header documents exit-code contract" "$([ $? -eq 0 ] && echo true || echo false)"
grep -q 'wall-check.sh' "$SHARED/agents/egress/export-tasks.md"
check "export-tasks.md calls wall-check.sh" "$([ $? -eq 0 ] && echo true || echo false)"
grep -q 'wall-check.sh' "$SHARED/skills/dispatch-tasks/SKILL.md"
check "dispatch-tasks SKILL.md calls wall-check.sh" "$([ $? -eq 0 ] && echo true || echo false)"
grep -q 'never blocks' "$SHARED/skills/dispatch-tasks/SKILL.md"
check "dispatch-tasks stays preview-only (never blocks)" "$([ $? -eq 0 ] && echo true || echo false)"

echo
echo "wall-check: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
