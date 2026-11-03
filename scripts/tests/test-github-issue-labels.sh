#!/usr/bin/env bash
# Tests github-issue.sh label handling with a stubbed `gh` that records argv.
# Never calls the real gh. Exit 0 = all pass, 1 = any failure.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../egress/github-issue.sh"

TMP="$(mktemp -d)"
cleanup() {
  rm -f "$TMP/bin/gh" "$TMP/argv"
  rmdir "$TMP/bin" "$TMP" 2>/dev/null || true
}
trap cleanup EXIT
mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'STUB'
#!/usr/bin/env bash
: > "$GH_ARGV_FILE"
for a in "$@"; do printf '%s\n' "$a" >> "$GH_ARGV_FILE"; done
echo "https://github.com/org/repo/issues/1"
STUB
chmod +x "$TMP/bin/gh"
export GH_ARGV_FILE="$TMP/argv"
export PATH="$TMP/bin:$PATH"

PASS=0
FAIL=0
check() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"; echo "    expected: $expected"; echo "    actual:   $actual"
    FAIL=$((FAIL + 1))
  fi
}
argv() { tr '\n' '|' < "$GH_ARGV_FILE"; }

echo "=== github-issue.sh labels ==="

echo "-- characterization: three-arg call (default path) --"
out=$(bash "$SCRIPT" "T" "B" "org/repo")
check "three-arg argv unchanged" \
  "issue|create|--title|T|--body|B|--label|triage|--repo|org/repo|" "$(argv)"
check "three-arg prints gh stdout" "https://github.com/org/repo/issues/1" "$out"

echo "-- four-arg --"
bash "$SCRIPT" "T" "B" "org/repo" "idea" >/dev/null
check "single label" \
  "issue|create|--title|T|--body|B|--label|idea|--repo|org/repo|" "$(argv)"

bash "$SCRIPT" "T" "B" "org/repo" "feature-request,triage" >/dev/null
check "two labels via comma list" \
  "issue|create|--title|T|--body|B|--label|feature-request,triage|--repo|org/repo|" "$(argv)"

echo "-- empty labels --"
bash "$SCRIPT" "T" "B" "org/repo" "" >/dev/null
check "empty labels falls back to triage" \
  "issue|create|--title|T|--body|B|--label|triage|--repo|org/repo|" "$(argv)"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ]
