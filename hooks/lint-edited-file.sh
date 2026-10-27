#!/bin/bash
# PostToolUse hook: lint the single file just edited, feed errors back to Claude.
#
# Project-dependent: uses whatever linter the project actually has. Silent no-op
# when none is found, so it never breaks a project that does not lint.
#
# Auto-detection per extension (single-file, fast, project-local binaries first):
#   JS/TS (.ts/.tsx/.js/.jsx/.mjs/.cjs): biome -> eslint
#   Python (.py):                        ruff -> flake8 -> pylint
#
# Override for ANY linter: drop a `.cc-lint` file at the repo root whose first
# line is a command template with a {file} placeholder, e.g.:
#     npx @biomejs/biome lint --diagnostic-level=error {file}
#     poetry run ruff check {file}
# The command runs from the repo root; {file} -> the edited path. Non-zero exit
# means errors, which get surfaced.
#
# Surfaces errors via exit 2 (PostToolUse feeds stderr back to Claude to fix).
# Disable: export CLAUDE_LINT_HOOK=0

[ "${CLAUDE_LINT_HOOK:-1}" = "0" ] && exit 0

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)
[ -z "$FILE" ] && exit 0
[ -f "$FILE" ] || exit 0

dir=$(dirname "$FILE")
ext="${FILE##*.}"

# Echo first existing "$dir/<relpath>" walking up to /.
find_up() {
  local d="$dir"
  while [ -n "$d" ] && [ "$d" != "/" ]; do
    [ -e "$d/$1" ] && { echo "$d/$1"; return 0; }
    d=$(dirname "$d")
  done
  return 1
}

TIMEOUT=""
command -v timeout >/dev/null 2>&1 && TIMEOUT="timeout 20"

OUT=""; STATUS=0; ran=0
run() { OUT=$($TIMEOUT "$@" 2>&1); STATUS=$?; ran=1; }

# 1) Project override: .cc-lint at any parent dir (works for ANY linter).
CCLINT=$(find_up ".cc-lint" || true)
if [ -n "$CCLINT" ]; then
  TPL=$(head -1 "$CCLINT")
  if [ -n "$TPL" ]; then
    CMD=${TPL//\{file\}/$FILE}
    OUT=$(cd "$(dirname "$CCLINT")" && eval "$TIMEOUT $CMD" 2>&1); STATUS=$?; ran=1
  fi
fi

# 2) Auto-detect by extension when no override ran.
if [ "$ran" -eq 0 ]; then
  case "$ext" in
    ts|tsx|js|jsx|mjs|cjs)
      if B=$(find_up "node_modules/.bin/biome"); then
        run "$B" lint --no-errors-on-unmatched --diagnostic-level=error "$FILE"
      elif E=$(find_up "node_modules/.bin/eslint"); then
        run "$E" "$FILE"
      fi
      ;;
    py)
      if R=$(command -v ruff 2>/dev/null || find_up ".venv/bin/ruff"); then
        run "$R" check "$FILE"
      elif F=$(command -v flake8 2>/dev/null || find_up ".venv/bin/flake8"); then
        run "$F" "$FILE"
      elif P=$(command -v pylint 2>/dev/null || find_up ".venv/bin/pylint"); then
        run "$P" --score=n "$FILE"
      fi
      ;;
  esac
fi

[ "$ran" -eq 0 ] && exit 0       # no linter for this file -> silent no-op
[ "$STATUS" -eq 0 ] && exit 0    # clean
[ "$STATUS" -eq 124 ] && exit 0  # timed out -> do not nag

echo "Lint errors in $(basename "$FILE") (fix before continuing):" >&2
echo "$OUT" | head -40 >&2
exit 2
