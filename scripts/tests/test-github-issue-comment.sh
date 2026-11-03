#!/usr/bin/env bash
# Tests for egress/github-issue-comment.sh. gh is stubbed via a PATH shim; the real gh is never called.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../egress/github-issue-comment.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'SHIM'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$GH_ARGS_FILE"
echo "https://github.com/org/repo/issues/12#issuecomment-1"
SHIM
chmod +x "$TMP/bin/gh"
export GH_ARGS_FILE="$TMP/gh-args"
export PATH="$TMP/bin:$PATH"

FAILS=0
fail() { echo "FAIL: $1"; FAILS=$((FAILS + 1)); }
pass() { echo "PASS: $1"; }

# 1. valid call invokes gh issue comment with number, body, --repo
rm -f "$GH_ARGS_FILE"
bash "$SCRIPT" 12 "seen again: flaky login, 2026-11-02" org/repo >/dev/null 2>"$TMP/err"
rc=$?
expected=$'issue\ncomment\n12\n--body\nseen again: flaky login, 2026-11-02\n--repo\norg/repo'
if [ "$rc" -eq 0 ] && [ "$(cat "$GH_ARGS_FILE" 2>/dev/null)" = "$expected" ]; then
  pass "valid call invokes gh issue comment"
else
  fail "valid call (rc=$rc, args: $(tr '\n' ' ' < "$GH_ARGS_FILE" 2>/dev/null))"
fi

# 2. missing args -> non-zero + usage on stderr, gh not called
for argv in "" "12" "12 body"; do
  rm -f "$GH_ARGS_FILE"
  # shellcheck disable=SC2086
  bash "$SCRIPT" $argv >/dev/null 2>"$TMP/err"; rc=$?
  if [ "$rc" -ne 0 ] && grep -q '^usage:' "$TMP/err" && [ ! -e "$GH_ARGS_FILE" ]; then
    pass "missing args rejected: '$argv'"
  else
    fail "missing args '$argv' (rc=$rc)"
  fi
done

# 3. empty args -> non-zero, gh not called
for i in 1 2 3; do
  args=(12 body org/repo); args[$((i - 1))]=""
  rm -f "$GH_ARGS_FILE"
  bash "$SCRIPT" "${args[@]}" >/dev/null 2>"$TMP/err"; rc=$?
  if [ "$rc" -ne 0 ] && [ -s "$TMP/err" ] && [ ! -e "$GH_ARGS_FILE" ]; then
    pass "empty arg $i rejected"
  else
    fail "empty arg $i (rc=$rc)"
  fi
done

# 4. non-numeric issue number rejected
for bad in abc 12a -3 1.5 "#12"; do
  rm -f "$GH_ARGS_FILE"
  bash "$SCRIPT" "$bad" body org/repo >/dev/null 2>"$TMP/err"; rc=$?
  if [ "$rc" -ne 0 ] && grep -q 'issue number' "$TMP/err" && [ ! -e "$GH_ARGS_FILE" ]; then
    pass "non-numeric '$bad' rejected"
  else
    fail "non-numeric '$bad' (rc=$rc)"
  fi
done

[ "$FAILS" -eq 0 ] || { echo "$FAILS failure(s)"; exit 1; }
echo "all passed"
