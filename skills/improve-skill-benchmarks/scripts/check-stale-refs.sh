#!/usr/bin/env bash
# Usage: check-stale-refs.sh <old-path>
# Greps ~/.dotfiles/claude-code-shared/ for references to the given path.
# Outputs file:line:match, one hit per line. Exit 0 always.

set -euo pipefail

OLD_PATH="${1:-}"
if [[ -z "$OLD_PATH" ]]; then
  echo "Usage: $0 <old-path>" >&2
  exit 1
fi

SHARED_ROOT="$HOME/.dotfiles/claude-code-shared"

grep -rn --include="*.md" --include="*.json" --include="*.sh" --include="*.py" --include="*.txt" \
  -F "$OLD_PATH" "$SHARED_ROOT" 2>/dev/null || true
