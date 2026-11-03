#!/usr/bin/env bash
# Tests for capture/find-dupes.sh. gh is stubbed via a PATH shim; the real gh is never called.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../capture/find-dupes.sh"

TMP="$(mktemp -d)"
trap 'rm -r "$TMP"' EXIT
mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'SHIM'
#!/usr/bin/env bash
printf '%s\n' "$@" >> "$GH_ARGS_FILE"
printf '%s\n' "---" >> "$GH_ARGS_FILE"
cat "$GH_OUT_FILE"
SHIM
chmod +x "$TMP/bin/gh"
export GH_ARGS_FILE="$TMP/gh-args" GH_OUT_FILE="$TMP/gh-out"
export PATH="$TMP/bin:$PATH"

FAILS=0
fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }
pass() { echo "PASS: $1"; }

# 1. gh called with target repo and --state open
echo '[]' > "$GH_OUT_FILE"; rm -f "$GH_ARGS_FILE"
bash "$SCRIPT" org/repo "login flaky" >/dev/null 2>&1; rc=$?
args="$(cat "$GH_ARGS_FILE" 2>/dev/null)"
if [ "$rc" -eq 0 ] && grep -qx 'issue' <<<"$args" && grep -qx 'list' <<<"$args" \
  && grep -A1 -x -- '--repo' <<<"$args" | grep -qx 'org/repo' \
  && grep -A1 -x -- '--state' <<<"$args" | grep -qx 'open' \
  && grep -A1 -x -- '--search' <<<"$args" | grep -qx 'login flaky'; then
  pass "gh called with repo, --state open, search"
else
  fail "gh argv (rc=$rc): $(tr '\n' ' ' <<<"$args")"
fi

# 2. empty list
out="$(bash "$SCRIPT" org/repo "x y" 2>/dev/null)"
[ "$out" = "[]" ] && pass "empty list" || fail "empty list: $out"

# 3. number/title/url returned, extra fields dropped
echo '[{"number":3,"title":"Flaky login","url":"https://github.com/org/repo/issues/3","labels":[]},{"number":9,"title":"Login slow","url":"https://github.com/org/repo/issues/9"}]' > "$GH_OUT_FILE"
out="$(bash "$SCRIPT" org/repo "login" 2>/dev/null)"
exp='[{"number":3,"title":"Flaky login","url":"https://github.com/org/repo/issues/3"},{"number":9,"title":"Login slow","url":"https://github.com/org/repo/issues/9"}]'
[ "$out" = "$exp" ] && pass "returns number,title,url" || fail "fields: $out"

# 4. only the passed repo is queried (single gh call)
rm -f "$GH_ARGS_FILE"
bash "$SCRIPT" other/thing "login" >/dev/null 2>&1
if [ "$(grep -c '^---$' "$GH_ARGS_FILE")" -eq 1 ] && grep -qx 'other/thing' "$GH_ARGS_FILE" && ! grep -qx 'org/repo' "$GH_ARGS_FILE"; then
  pass "queries only the passed repo"
else
  fail "repo scoping: $(tr '\n' ' ' < "$GH_ARGS_FILE")"
fi

# 5. bad args rejected without calling gh
for argv in "" "org/repo" "norepo terms"; do
  rm -f "$GH_ARGS_FILE"
  # shellcheck disable=SC2086
  bash "$SCRIPT" $argv >/dev/null 2>&1; rc=$?
  if [ "$rc" -ne 0 ] && [ ! -e "$GH_ARGS_FILE" ]; then pass "rejected: '$argv'"; else fail "reject '$argv' rc=$rc"; fi
done

# 6. term extraction
out="$(bash "$SCRIPT" --extract-terms "The Login page is flaky, and the login fails!")"
[ "$out" = "login page flaky fails" ] && pass "extract terms" || fail "extract: '$out'"

[ "$FAILS" -eq 0 ] || { echo "$FAILS failed"; exit 1; }
echo "all passed"
