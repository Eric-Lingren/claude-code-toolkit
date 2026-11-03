#!/usr/bin/env bash
# Tests for capture/capture-file.sh. gh, osascript and the adapters are stubbed; real gh is never called.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
S="$HERE/../capture/capture-file.sh"

TMP="$(mktemp -d)"
trap 'rm -r "$TMP"' EXIT
mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'SHIM'
#!/usr/bin/env bash
echo "gh $*" >> "$TMP_CALLS"
cat "$GH_OUT_FILE"
SHIM
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/bin/osascript"
cat > "$TMP/adapter" <<'SHIM'
#!/usr/bin/env bash
printf 'new|%s|%s|%s\n' "$1" "$3" "$4" >> "$ADAPTER_LOG"
if [ -n "${ADAPTER_FAIL:-}" ]; then echo "gh auth expired" >&2; exit 1; fi
echo "https://github.com/$3/issues/42"
SHIM
cat > "$TMP/comment-adapter" <<'SHIM'
#!/usr/bin/env bash
printf 'comment|%s\n' "$*" >> "$ADAPTER_LOG"
echo "https://github.com/$3/issues/$1#issuecomment-1"
SHIM
chmod +x "$TMP/bin/gh" "$TMP/bin/osascript" "$TMP/adapter" "$TMP/comment-adapter"
export TMP_CALLS="$TMP/calls" ADAPTER_LOG="$TMP/adapter.log" GH_OUT_FILE="$TMP/gh-out"
export CAPTURE_ADAPTER="$TMP/adapter" CAPTURE_COMMENT_ADAPTER="$TMP/comment-adapter"
export CAPTURE_OUTBOX_DIR="$TMP/outbox"
export PATH="$TMP/bin:$PATH"
echo '[]' > "$GH_OUT_FILE"

DOT=Eric-Lingren/dotfiles
# Corporate domain + repo come from the real (private) policy so no org name is hardcoded.
CORP=$(jq -r '.destinations | to_entries[] | select(.value.wall == "corporate") | .key' "$HERE/../../resources/task-routing.json" | head -1)
CORP_REPO=$(jq -r --arg d "$CORP" 'to_entries[] | select(.value.domain? == $d) | .key' "$HERE/../../resources/repo-policy.json" | head -1)
[ -n "$CORP" ] && [ -n "$CORP_REPO" ] || { echo "FAIL: no corporate domain/repo in real policy"; exit 1; }
FAILS=0
fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }
pass() { echo "PASS: $1"; }
reset() { rm -f "$ADAPTER_LOG" "$TMP_CALLS"; rm -rf "$CAPTURE_OUTBOX_DIR"; unset ADAPTER_FAIL; }

mk() { # name label sealed [where session]
  cat > "$TMP/$1.json" <<EOF
{"title":"Review watcher misses rebases","label":"$2","sealed":$3,"what":"Watcher misses rebases.","why":"Noticed during a review.","where":"${4:-}","session":"${5:-}"}
EOF
}

# 1. unsealed capture to dotfiles ends in an adapter call with resolved labels
reset; mk a fix false "claude-code-shared/x.sh:1" "sess-1"
out="$(bash "$S" prepare "$TMP/a.json" --target $DOT --source $DOT 2>&1)"; rc=$?
[ $rc -eq 0 ] && grep -q "y / edit / cancel" <<<"$out" && grep -q "\[fix\]" <<<"$out" && pass "prepare ok with preview" || fail "prepare rc=$rc: $out"
out="$(bash "$S" file "$TMP/a.json" --target $DOT 2>&1)"; rc=$?
if [ $rc -eq 0 ] && grep -qx 'new|Review watcher misses rebases|Eric-Lingren/dotfiles|fix' "$ADAPTER_LOG" && [ "$out" = "✓ captured #42 [fix] https://github.com/$DOT/issues/42" ]; then
  pass "adapter called with labels from resolve-labels"
else fail "adapter call rc=$rc out=$out log=$(cat "$ADAPTER_LOG" 2>/dev/null)"; fi

# 2. corporate source: unsealed demands sealing, sealed has no where/session section
reset; mk b idea false "path/x" "sess"
bash "$S" prepare "$TMP/b.json" --target $DOT --source "$CORP_REPO" >/dev/null 2>&1; rc=$?
[ $rc -eq 10 ] && pass "corporate unsealed -> exit 10" || fail "expected 10 got $rc"
mk b idea true
out="$(bash "$S" prepare "$TMP/b.json" --target $DOT --source "$CORP_REPO" 2>&1)"; rc=$?
body="$(python3 "$HERE/../capture/validate-capture.py" "$TMP/b.json")"
if [ $rc -eq 0 ] && grep -q "⚠ sealed (from $CORP_REPO)" <<<"$out" \
  && ! grep -qi 'where' <<<"$body" && ! grep -qi 'session' <<<"$body"; then
  pass "corporate source sealed, body has no where/session"
else fail "sealed rc=$rc out=$out body=$body"; fi

# 3. wall-check blocked aborts before any adapter call
reset; mk c idea false
out="$(bash "$S" prepare "$TMP/c.json" --target Eric-Lingren/ericlingren --source $DOT 2>&1)"; rc=$?
# ericlingren is not a capture target -> abort; a hard wall block (non-corporate to corporate) also aborts
out2="$(bash "$S" prepare "$TMP/c.json" --target $DOT --source nonexistent/repo 2>&1)"; rc2=$?
if [ $rc -ne 0 ] && [ $rc2 -ne 0 ] && [ ! -e "$ADAPTER_LOG" ]; then pass "blocked/invalid aborts, no adapter call"; else fail "rc=$rc rc2=$rc2 $out2"; fi

# 4. approved match comments; no --comment (Nnew) creates a new issue
reset; mk d fix false
echo '[{"number":7,"title":"Review watcher misses rebases","url":"https://github.com/Eric-Lingren/dotfiles/issues/7"}]' > "$GH_OUT_FILE"
out="$(bash "$S" prepare "$TMP/d.json" --target $DOT --source $DOT 2>&1)"
grep -q '≈#7' <<<"$out" && pass "dupe marked ≈#7" || fail "no dupe marker: $out"
out="$(bash "$S" file "$TMP/d.json" --target $DOT --comment 7 2>&1)"; rc=$?
if [ $rc -eq 0 ] && grep -q '^comment|7 seen again: ' "$ADAPTER_LOG" && ! grep -q '^new|' "$ADAPTER_LOG" && grep -q '^✓ captured #7 \[fix\]' <<<"$out"; then
  pass "approved ≈ match posts a comment, not an issue"
else fail "comment rc=$rc out=$out log=$(cat "$ADAPTER_LOG")"; fi
rm -f "$ADAPTER_LOG"
bash "$S" file "$TMP/d.json" --target $DOT >/dev/null 2>&1
grep -q '^new|' "$ADAPTER_LOG" && ! grep -q '^comment|' "$ADAPTER_LOG" && pass "Nnew forces a new issue" || fail "Nnew: $(cat "$ADAPTER_LOG")"
echo '[]' > "$GH_OUT_FILE"

# 5. adapter failure enqueues and prints a single ERROR line; flush retries
reset; mk e idea false "w" "s"
export ADAPTER_FAIL=1
out="$(bash "$S" file "$TMP/e.json" --target $DOT 2>&1)"; rc=$?
lines=$(printf '%s\n' "$out" | wc -l | tr -d ' ')
queued=$(find "$CAPTURE_OUTBOX_DIR" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
if [ $rc -ne 0 ] && [ "$lines" = 1 ] && [[ "$out" == ERROR:* ]] && [ "$queued" = 1 ]; then
  pass "failure: single ERROR line and queued"
else fail "failure rc=$rc lines=$lines queued=$queued out=$out"; fi
unset ADAPTER_FAIL
out="$(bash "$S" flush 2>&1)"
queued=$(find "$CAPTURE_OUTBOX_DIR" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
[ "$queued" = 0 ] && grep -q '^new|' "$ADAPTER_LOG" && pass "flush refiles queued capture" || fail "flush queued=$queued out=$out"

# 6. finish handles agent responses
reset; mk f idea false
out="$(bash "$S" finish "$TMP/f.json" --target $DOT "https://github.com/$DOT/issues/9")"
[ "$out" = "✓ captured #9 [idea] https://github.com/$DOT/issues/9" ] && pass "finish success line" || fail "finish: $out"
out="$(bash "$S" finish "$TMP/f.json" --target $DOT "ERROR: label missing")"; rc=$?
[ $rc -ne 0 ] && [ "$out" = "ERROR: label missing" ] && pass "finish error line" || fail "finish err: $out"

[ $FAILS -eq 0 ] && echo "all passed" || echo "$FAILS failed"
exit $FAILS
