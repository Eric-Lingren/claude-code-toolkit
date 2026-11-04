#!/usr/bin/env bash
# Tests for attribution/change-revert.sh (change-revert.sh <change_id>)
# Builds a throwaway origin + clone per case. gh is a fake binary on PATH that records
# its argv; the real gh is never invoked.
#
# Usage: bash test-change-revert.sh
# Exit 0 = all tests passed. Exit 1 = one or more failed.

set -uo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$SCRIPTS_DIR/attribution/change-revert.sh"
DOTFILES_ROOT="$(cd "$SCRIPTS_DIR/../.." && pwd)"

PASS=0
FAIL=0
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

assert_pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
assert_fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }
assert_eq() { if [ "$1" = "$2" ]; then assert_pass "$3"; else assert_fail "$3 (expected '$2', got '$1')"; fi; }
assert_contains() { if printf '%s' "$1" | grep -qF -- "$2"; then assert_pass "$3"; else assert_fail "$3 ('$2' not in: $1)"; fi; }
assert_not_contains() { if printf '%s' "$1" | grep -qF -- "$2"; then assert_fail "$3 ('$2' in: $1)"; else assert_pass "$3"; fi; }

FAKE_BIN="$TMP/bin"
mkdir -p "$FAKE_BIN"
cat > "$FAKE_BIN/gh" <<'FAKE'
#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_LOG"
if [ -n "${FAKE_GH_FAIL:-}" ] && [ "$1 $2" = "pr create" ]; then
  echo "gh: simulated pr create failure" >&2
  exit 1
fi
case "$1 $2" in
  "pr create") echo "https://github.com/o/r/pull/999" ;;
esac
exit 0
FAKE
chmod +x "$FAKE_BIN/gh"

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

# new_repo <name>: bare origin + clone on main with one file. Sets WORK, ORIGIN, GH_LOG.
new_repo() {
  ORIGIN="$TMP/$1-origin.git"; WORK="$TMP/$1-work"; GH_LOG="$TMP/$1-gh.log"
  git init -q --bare -b main "$ORIGIN"
  git clone -q "$ORIGIN" "$WORK" 2>/dev/null
  git -C "$WORK" checkout -q -b main 2>/dev/null
  mkdir -p "$WORK/claude-code-shared/scripts" "$WORK/attribution/change-records"
  printf 'one\ntwo\nthree\n' > "$WORK/claude-code-shared/scripts/a.txt"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "initial"
  git -C "$WORK" push -q -u origin main 2>/dev/null
  : > "$GH_LOG"
}

# write_record <sha>: writes the change record for a merged sha; echoes the change_id.
write_record() {
  local sha="$1" id="chg-${1:0:12}"
  cat > "$WORK/attribution/change-records/$id.json" <<JSON
{"schema_version":"1","change_id":"$id","lane":"manual","source_refs":["pr:1"],
 "target_repo":"o/r","touched_paths":[],"sections":[],"touched_units":[],
 "kind":"script","base_sha":"$(git -C "$WORK" rev-parse "$sha^")","merged_sha":"$sha",
 "merged_at":"2026-11-01T00:00:00-08:00"}
JSON
  printf '%s' "$id"
}

# run_revert <args...>: runs change-revert.sh against $WORK with fake gh. Sets OUT, RC.
run_revert() {
  OUT=$(cd "$WORK" && PATH="$FAKE_BIN:$PATH" FAKE_GH_LOG="$GH_LOG" CHANGE_REVERT_REPO="$WORK" \
    CHANGE_REVERT_CHANGES_DIR="${CHANGE_REVERT_CHANGES_DIR:-$WORK/attribution/change-records}" bash "$SCRIPT" "$@" 2>&1)
  RC=$?
}

tree_clean() { [ -z "$(git -C "$WORK" status --porcelain)" ]; }

echo "== squash-commit record =="
new_repo squash
printf 'one\nTWO\nthree\n' > "$WORK/claude-code-shared/scripts/a.txt"
git -C "$WORK" commit -q -am "Change line two"
SHA=$(git -C "$WORK" rev-parse HEAD)
ID=$(write_record "$SHA")
git -C "$WORK" add -A; git -C "$WORK" commit -q -m "add record"
git -C "$WORK" push -q origin main 2>/dev/null
HEAD_BEFORE=$(git -C "$WORK" rev-parse HEAD)
CHANGE_REVERT_BASE_BRANCH=no-such-branch run_revert "$ID"
[ "$RC" -ne 0 ]; assert_eq "$?" "0" "CHANGE_REVERT_BASE_BRANCH is honored (missing branch fails)"
assert_contains "$OUT" "base branch 'no-such-branch' not found" "CHANGE_REVERT_BASE_BRANCH name appears in the error"
run_revert "$ID"
assert_eq "$RC" "0" "squash revert exits 0 ($OUT)"
git -C "$WORK" rev-parse --verify -q "revert/$ID" >/dev/null
assert_eq "$?" "0" "revert branch revert/$ID created locally"
assert_eq "$(git -C "$WORK" show "revert/$ID:claude-code-shared/scripts/a.txt" | sed -n 2p)" "two" "revert branch restores the original line"
assert_contains "$(git -C "$WORK" log -1 --format=%s "revert/$ID")" "Revert" "tip commit is a revert commit"
assert_eq "$(git -C "$WORK" rev-list --parents -n1 "revert/$ID" | wc -w | tr -d ' ')" "2" "revert commit has a single parent"
assert_eq "$(git -C "$WORK" rev-parse HEAD)" "$HEAD_BEFORE" "main checkout HEAD unchanged"
assert_eq "$(git -C "$WORK" rev-parse --abbrev-ref HEAD)" "main" "main checkout still on main"
tree_clean; assert_eq "$?" "0" "main checkout tree clean after success"
git -C "$ORIGIN" rev-parse --verify -q "revert/$ID" >/dev/null
assert_eq "$?" "0" "revert branch pushed to origin"
assert_eq "$(git -C "$WORK" worktree list | wc -l | tr -d ' ')" "1" "temporary worktree removed"

echo "== PR opened via gh pr create, never merged =="
assert_eq "$(grep -c '^pr create' "$GH_LOG")" "1" "gh pr create called exactly once"
assert_contains "$(cat "$GH_LOG")" "--head revert/$ID" "PR head is the revert branch"
assert_contains "$(cat "$GH_LOG")" "--base main" "PR targets main"
assert_eq "$(grep -c -e '^pr merge' -e '--auto' -e '--merge' "$GH_LOG")" "0" "gh never asked to merge"
assert_contains "$OUT" "https://github.com/o/r/pull/999" "PR url printed"
assert_eq "$(git -C "$ORIGIN" rev-parse main)" "$HEAD_BEFORE" "origin main untouched (not merged)"

echo "== merge-commit record uses -m 1 =="
new_repo merge
git -C "$WORK" checkout -q -b feat/x
printf 'one\ntwo\nthree\nfour\n' > "$WORK/claude-code-shared/scripts/a.txt"
git -C "$WORK" commit -q -am "feature: add four"
printf 'five\n' > "$WORK/claude-code-shared/scripts/b.txt"
git -C "$WORK" add -A; git -C "$WORK" commit -q -m "feature: add b"
git -C "$WORK" checkout -q main
git -C "$WORK" merge -q --no-ff feat/x -m "Merge pull request #5 from o/feat/x"
SHA=$(git -C "$WORK" rev-parse HEAD)
ID=$(write_record "$SHA")
git -C "$WORK" add -A; git -C "$WORK" commit -q -m "add record"
git -C "$WORK" push -q origin main 2>/dev/null
run_revert "$ID"
assert_eq "$RC" "0" "merge revert exits 0 ($OUT)"
assert_contains "$(git -C "$WORK" log -1 --format=%B "revert/$ID")" "reversing" "revert commit message shows mainline parent was chosen (-m)"
assert_eq "$(git -C "$WORK" show "revert/$ID:claude-code-shared/scripts/a.txt" | wc -l | tr -d ' ')" "3" "merged edit to a.txt reverted"
git -C "$WORK" cat-file -e "revert/$ID:claude-code-shared/scripts/b.txt" 2>/dev/null
assert_eq "$?" "128" "file added by the merge is removed"
assert_eq "$(git -C "$WORK" rev-list --parents -n1 "revert/$ID" | wc -w | tr -d ' ')" "2" "revert commit is a plain single-parent commit"
assert_eq "$(grep -c '^pr create' "$GH_LOG")" "1" "PR opened for merge revert"

echo "== unknown change_id =="
new_repo unknown
HEAD_BEFORE=$(git -C "$WORK" rev-parse HEAD)
run_revert chg-000000000000
[ "$RC" -ne 0 ]; assert_eq "$?" "0" "unknown id exits non-zero"
assert_contains "$OUT" "chg-000000000000" "message names the unknown id"
assert_contains "$OUT" "no change record" "message says there is no change record"
HELPER_DIR=$(python3 "$SCRIPTS_DIR/shared/change_records_path.py")
OUT=$(cd "$WORK" && CHANGE_REVERT_REPO="$WORK" bash "$SCRIPT" chg-000000000000 2>&1)
assert_contains "$OUT" "no change record" "default folder: unknown id reports no change record"
assert_contains "$OUT" "looked in $HELPER_DIR" "default folder in the error is the helper folder"
assert_eq "$(git -C "$WORK" branch --list 'revert/*' | wc -l | tr -d ' ')" "0" "no revert branch created"
assert_eq "$(wc -c < "$GH_LOG" | tr -d ' ')" "0" "gh not called"
assert_eq "$(git -C "$WORK" rev-parse HEAD)" "$HEAD_BEFORE" "HEAD unchanged"
tree_clean; assert_eq "$?" "0" "tree clean"
run_revert "not-an-id"
[ "$RC" -ne 0 ]; assert_eq "$?" "0" "malformed id exits non-zero"
run_revert
[ "$RC" -ne 0 ]; assert_eq "$?" "0" "no args exits non-zero"
assert_contains "$OUT" "usage: change-revert.sh <change_id>" "no args prints usage"
run_revert bogus
[ "$RC" -ne 0 ]; assert_eq "$?" "0" "unknown command exits non-zero"
assert_contains "$OUT" "usage: change-revert.sh <change_id>" "unknown command prints usage"
run_revert revert "$ID"
[ "$RC" -ne 0 ]; assert_eq "$?" "0" "old 'revert' word exits non-zero"
assert_contains "$OUT" "usage: change-revert.sh <change_id>" "old 'revert' word prints usage"
assert_eq "$(git -C "$WORK" branch --list 'revert/*' | wc -l | tr -d ' ')" "0" "old 'revert' word creates no branch"
CHANGE_REVERT_CHANGES_DIR="$TMP/no-such-records" run_revert "$ID"
[ "$RC" -ne 0 ]; assert_eq "$?" "0" "CHANGE_REVERT_CHANGES_DIR is honored (missing dir fails)"
assert_contains "$OUT" "$TMP/no-such-records" "CHANGE_REVERT_CHANGES_DIR path appears in the error"

echo "== conflicting revert =="
new_repo conflict
printf 'one\nTWO\nthree\n' > "$WORK/claude-code-shared/scripts/a.txt"
git -C "$WORK" commit -q -am "Change line two"
SHA=$(git -C "$WORK" rev-parse HEAD)
printf 'one\nDEUX\nthree\n' > "$WORK/claude-code-shared/scripts/a.txt"
git -C "$WORK" commit -q -am "Change line two again"
ID=$(write_record "$SHA")
git -C "$WORK" add -A; git -C "$WORK" commit -q -m "add record"
git -C "$WORK" push -q origin main 2>/dev/null
HEAD_BEFORE=$(git -C "$WORK" rev-parse HEAD)
run_revert "$ID"
[ "$RC" -ne 0 ]; assert_eq "$?" "0" "conflicting revert exits non-zero"
assert_contains "$OUT" "conflict" "message mentions the conflict"
tree_clean; assert_eq "$?" "0" "main checkout tree clean after conflict"
assert_eq "$(git -C "$WORK" rev-parse HEAD)" "$HEAD_BEFORE" "HEAD unchanged after conflict"
assert_eq "$(git -C "$WORK" branch --list 'revert/*' | wc -l | tr -d ' ')" "0" "revert branch removed"
assert_eq "$(git -C "$WORK" worktree list | wc -l | tr -d ' ')" "1" "temporary worktree removed"
git -C "$ORIGIN" rev-parse --verify -q "revert/$ID" >/dev/null
assert_eq "$?" "1" "nothing pushed"
assert_eq "$(wc -c < "$GH_LOG" | tr -d ' ')" "0" "gh not called after conflict"

echo "== commit not in this repo =="
new_repo missing
HEAD_BEFORE=$(git -C "$WORK" rev-parse HEAD)
MISSING_SHA=$(printf 'deadbeef%.0s' 1 2 3 4 5)
MISSING_ID="chg-${MISSING_SHA:0:12}"
cat > "$WORK/attribution/change-records/$MISSING_ID.json" <<JSON
{"schema_version":"1","change_id":"$MISSING_ID","merged_sha":"$MISSING_SHA"}
JSON
run_revert "$MISSING_ID"
[ "$RC" -ne 0 ]; assert_eq "$?" "0" "unknown commit exits non-zero"
assert_contains "$OUT" "is not in this repo" "message says the commit is not in this repo"
assert_eq "$(git -C "$WORK" branch --list 'revert/*' | wc -l | tr -d ' ')" "0" "no revert branch created"
assert_eq "$(wc -c < "$GH_LOG" | tr -d ' ')" "0" "gh not called"
assert_eq "$(git -C "$WORK" rev-parse HEAD)" "$HEAD_BEFORE" "HEAD unchanged"
assert_eq "$(git -C "$WORK" worktree list | wc -l | tr -d ' ')" "1" "no temporary worktree created"

echo "== commit not on the base branch =="
new_repo offbase
git -C "$WORK" checkout -q -b feat/unmerged
printf 'one\nTWO\nthree\n' > "$WORK/claude-code-shared/scripts/a.txt"
git -C "$WORK" commit -q -am "Unmerged change"
SHA=$(git -C "$WORK" rev-parse HEAD)
ID=$(write_record "$SHA")
git -C "$WORK" checkout -q main
git -C "$WORK" add -A; git -C "$WORK" commit -q -m "add record"
git -C "$WORK" push -q origin main 2>/dev/null
HEAD_BEFORE=$(git -C "$WORK" rev-parse HEAD)
run_revert "$ID"
[ "$RC" -ne 0 ]; assert_eq "$?" "0" "off-base commit exits non-zero"
assert_contains "$OUT" "nothing to revert" "message says there is nothing to revert"
assert_contains "$OUT" "is not on" "message says the commit is not on the base branch"
assert_eq "$(git -C "$WORK" branch --list 'revert/*' | wc -l | tr -d ' ')" "0" "no revert branch created"
assert_eq "$(wc -c < "$GH_LOG" | tr -d ' ')" "0" "gh not called"
assert_eq "$(git -C "$WORK" rev-parse HEAD)" "$HEAD_BEFORE" "HEAD unchanged"
git -C "$ORIGIN" rev-parse --verify -q "revert/$ID" >/dev/null
assert_eq "$?" "1" "nothing pushed"
assert_eq "$(git -C "$WORK" worktree list | wc -l | tr -d ' ')" "1" "no temporary worktree created"

echo "== gh pr create fails after the push =="
new_repo ghfail
printf 'one\nTWO\nthree\n' > "$WORK/claude-code-shared/scripts/a.txt"
git -C "$WORK" commit -q -am "Change line two"
SHA=$(git -C "$WORK" rev-parse HEAD)
ID=$(write_record "$SHA")
git -C "$WORK" add -A; git -C "$WORK" commit -q -m "add record"
git -C "$WORK" push -q origin main 2>/dev/null
HEAD_BEFORE=$(git -C "$WORK" rev-parse HEAD)
FAKE_GH_FAIL=1 run_revert "$ID"
assert_eq "$RC" "1" "gh failure exits 1 ($OUT)"
assert_contains "$OUT" "was pushed but gh pr create failed" "message says the branch was pushed and gh failed"
assert_contains "$OUT" "Open the PR by hand" "message tells the user to open the PR by hand"
assert_eq "$(grep -c '^pr create' "$GH_LOG")" "1" "gh pr create attempted once"
git -C "$WORK" rev-parse --verify -q "revert/$ID" >/dev/null
assert_eq "$?" "0" "local revert branch survives"
git -C "$ORIGIN" rev-parse --verify -q "revert/$ID" >/dev/null
assert_eq "$?" "0" "revert branch stays pushed on origin"
assert_eq "$(git -C "$WORK" worktree list | wc -l | tr -d ' ')" "1" "temporary worktree removed"
assert_eq "$(git -C "$WORK" rev-parse HEAD)" "$HEAD_BEFORE" "main checkout HEAD unchanged"
tree_clean; assert_eq "$?" "0" "main checkout tree clean after gh failure"
run_revert "$ID"
assert_eq "$RC" "1" "rerun after gh failure exits 1"
assert_contains "$OUT" "branch revert/$ID already exists" "rerun dies with 'branch already exists'"
assert_eq "$(grep -c '^pr create' "$GH_LOG")" "1" "rerun does not call gh again"

echo "== push to origin is rejected =="
new_repo pushfail
printf 'one\nTWO\nthree\n' > "$WORK/claude-code-shared/scripts/a.txt"
git -C "$WORK" commit -q -am "Change line two"
SHA=$(git -C "$WORK" rev-parse HEAD)
ID=$(write_record "$SHA")
git -C "$WORK" add -A; git -C "$WORK" commit -q -m "add record"
git -C "$WORK" push -q origin main 2>/dev/null
HEAD_BEFORE=$(git -C "$WORK" rev-parse HEAD)
mkdir -p "$ORIGIN/hooks"
printf '#!/bin/sh\necho "rejected by test hook" >&2\nexit 1\n' > "$ORIGIN/hooks/pre-receive"
chmod +x "$ORIGIN/hooks/pre-receive"
run_revert "$ID"
assert_eq "$RC" "1" "push failure exits 1 ($OUT)"
assert_contains "$OUT" "could not push revert/$ID to origin" "message says the push failed"
git -C "$WORK" rev-parse --verify -q "refs/heads/revert/$ID" >/dev/null
assert_eq "$?" "1" "no local revert branch left"
git -C "$ORIGIN" rev-parse --verify -q "revert/$ID" >/dev/null
assert_eq "$?" "1" "nothing landed on origin"
assert_eq "$(git -C "$WORK" worktree list | wc -l | tr -d ' ')" "1" "no leftover worktree"
assert_eq "$(wc -c < "$GH_LOG" | tr -d ' ')" "0" "gh never called after push failure"
assert_eq "$(git -C "$WORK" rev-parse HEAD)" "$HEAD_BEFORE" "main checkout HEAD unchanged"
tree_clean; assert_eq "$?" "0" "main checkout tree clean after push failure"

echo "== exposure =="
assert_contains "$(grep -n '^alias change-revert=' "$DOTFILES_ROOT/.zshrc")" "claude-code-shared/scripts/attribution/change-revert.sh" ".zshrc aliases change-revert to change-revert.sh"
assert_eq "$(grep -c '^alias bank=' "$DOTFILES_ROOT/.zshrc")" "0" ".zshrc defines no alias named bank"
test ! -e "$SCRIPTS_DIR/bank"; assert_eq "$?" "0" "scripts/bank no longer exists"
test -x "$SCRIPT"; assert_eq "$?" "0" "change-revert.sh is executable"

echo
echo "Passed: $PASS  Failed: $FAIL"
[ "$FAIL" -eq 0 ]
