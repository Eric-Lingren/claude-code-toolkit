#!/usr/bin/env bash
# Tests for capture/setup-labels.sh. gh is stubbed via PATH shim; never touches the real repo.
set -u
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/capture/setup-labels.sh"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'SH'
#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$1 $2" in
  "label list") cat "$GH_LABELS" ;;
  "issue list")
    lbl=""; while [ $# -gt 0 ]; do [ "$1" = "--label" ] && lbl="$2"; shift; done
    if [ "$lbl" = "${GH_USED_LABEL:-}" ]; then printf '12\n34\n'; fi ;;
  "label create"|"label delete") ;;
  *) echo "unexpected gh call: $*" >&2; exit 9 ;;
esac
SH
chmod +x "$TMP/bin/gh"
export PATH="$TMP/bin:$PATH" GH_LOG="$TMP/log" GH_LABELS="$TMP/labels"
printf 'bug\ndocumentation\ntriage\nquestion\nwontfix\n' > "$GH_LABELS"
fails=0
pass() { echo "ok - $1"; }
fail() { echo "FAIL - $1"; echo "--- output: ${out:-}"; fails=$((fails+1)); }
reset() { : > "$GH_LOG"; unset GH_USED_LABEL; }
mutated() { grep -Eq 'label (create|delete)' "$GH_LOG"; }

reset
out="$("$SCRIPT" 2>&1)"; rc=$?
[ $rc -eq 0 ] && pass "dry-run exits 0" || fail "dry-run exits 0"
{ grep -q 'create label: idea' <<<"$out" && grep -q 'create label: fix' <<<"$out"; } \
  && pass "dry-run prints create idea/fix" || fail "dry-run prints create idea/fix"
grep -q 'delete label: bug' <<<"$out" && pass "dry-run prints delete bug" || fail "dry-run prints delete bug"
mutated && fail "dry-run no mutating gh" || pass "dry-run no mutating gh"

reset; export GH_USED_LABEL=bug
out="$("$SCRIPT" --apply 2>&1)"; rc=$?
[ $rc -ne 0 ] && pass "apply aborts nonzero on used label" || fail "apply aborts nonzero on used label"
grep -q 'bug: #12 #34' <<<"$out" && pass "apply lists offender" || fail "apply lists offender"
mutated && fail "apply abort mutates nothing" || pass "apply abort mutates nothing"

reset
out="$("$SCRIPT" --apply 2>&1)"; rc=$?
[ $rc -eq 0 ] && pass "apply exits 0" || fail "apply exits 0"
{ grep -q 'label create idea' "$GH_LOG" && grep -q 'label create fix' "$GH_LOG"; } \
  && pass "apply creates idea and fix" || fail "apply creates idea and fix"
{ grep -q 'label create learnings-lane' "$GH_LOG" && grep -q 'label create blocked' "$GH_LOG" \
  && grep -q 'label create needs-grill' "$GH_LOG" && grep -q 'label create system-generated' "$GH_LOG"; } \
  && pass "apply creates lane labels" || fail "apply creates lane labels"
{ grep -q 'label delete bug' "$GH_LOG" && grep -q 'label delete wontfix' "$GH_LOG"; } \
  && pass "apply deletes unused defaults" || fail "apply deletes unused defaults"
grep -q 'label delete triage' "$GH_LOG" && fail "apply never deletes triage" || pass "apply never deletes triage"

reset
out="$("$SCRIPT" --apply --repo Eric-Lingren/SpawnedSapien 2>&1)"; rc=$?
{ [ $rc -ne 0 ] && grep -q refusing <<<"$out"; } && pass "refuses other repo" || fail "refuses other repo"
[ ! -s "$GH_LOG" ] && pass "refusal makes no gh calls" || fail "refusal makes no gh calls"

[ "$fails" -eq 0 ] || { echo "$fails failure(s)"; exit 1; }
echo "all passed"
