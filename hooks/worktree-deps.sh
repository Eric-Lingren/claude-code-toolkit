#!/bin/bash
# PostToolUse hook for EnterWorktree: link local config and install JS deps in new worktrees.
#
# Claude Code's isolation: "worktree" creates bare git worktrees at
# .claude/worktrees/agent-*. These have no node_modules, so pre-commit
# hooks (biome, eslint) and builds fail. This hook detects the package
# manager and runs install after worktree creation, via the shared
# ensure-worktree-deps.sh script.
#
# Disable: export CLAUDE_WT_DEPS=0

[ "${CLAUDE_WT_DEPS:-1}" = "0" ] && exit 0

INPUT=$(cat)

# Try to get worktree path from tool input (existing worktree entry)
WT_PATH=$(echo "$INPUT" | jq -r '.tool_input.path // empty' 2>/dev/null)

# For new worktrees, construct path from name + git root
if [ -z "$WT_PATH" ] || [ ! -d "$WT_PATH" ]; then
  NAME=$(echo "$INPUT" | jq -r '.tool_input.name // empty' 2>/dev/null)
  if [ -n "$NAME" ]; then
    ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
    [ -n "$ROOT" ] && WT_PATH="$ROOT/.claude/worktrees/$NAME"
  fi
fi

# Fall back to CWD (session switches into worktree after EnterWorktree)
if [ -z "$WT_PATH" ] || [ ! -d "$WT_PATH" ]; then
  WT_PATH="$PWD"
fi

[ -d "$WT_PATH" ] || exit 0

# Symlink gitignored local config (app/.env etc.) per repo-policy worktree_links.
bash "$HOME/.claude-code-shared/scripts/shared/ensure-worktree-links.sh" "$WT_PATH" >&2

[ -f "$WT_PATH/package.json" ] || exit 0

# Repairs symlinked node_modules and installs only when needed.
bash "$HOME/.claude-code-shared/scripts/shared/ensure-worktree-deps.sh" --quiet "$WT_PATH" >&2

# Never block worktree entry on install failure
exit 0
