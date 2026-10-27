#!/bin/bash
# PreToolUse hook: blocks symlinking node_modules between checkouts.
# Catches: ln -s / ln -sf / ln -sfn ... node_modules, and os.symlink /
# fs.symlinkSync calls that mention node_modules.
# Why: Turbopack rejects node_modules links that leave the project root, and
# pnpm install through the link rewrites the other checkout's package links.
# Exits 2 to block, 0 to allow.
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Check each shell segment on its own so an unrelated ln elsewhere in a
# compound command does not trip the match.
MATCHED=$(printf '%s\n' "$COMMAND" | tr ';&|' '\n\n\n' | grep -E \
  "(^|[[:space:](])ln[[:space:]]+(-[a-zA-Z]*s[a-zA-Z]*[[:space:]]+)+.*node_modules\
|(^|[[:space:](])ln[[:space:]]+.*--symbolic.*node_modules\
|(os\.symlink|symlinkSync|fs\.symlink)\(.*node_modules" \
  | head -1)

if [ -n "$MATCHED" ]; then
  echo "Blocked: symlinking node_modules ('$(echo "$MATCHED" | sed 's/^[[:space:]]*//')')." >&2
  echo "" >&2
  echo "Worktrees need their own node_modules. Run this instead:" >&2
  echo "" >&2
  echo "  ~/.dotfiles/claude-code-shared/scripts/shared/ensure-worktree-deps.sh [worktree-path]" >&2
  exit 2
fi

exit 0
