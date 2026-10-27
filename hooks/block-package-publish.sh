#!/bin/bash
# PreToolUse hook: blocks accidental package publishing.
# Catches: npm/yarn/pnpm publish, twine upload, gem push, cargo publish.
# Allows install, add, build, and all other package manager commands.
# Exits 2 to block, 0 to allow.
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

MATCHED=$(echo "$COMMAND" | grep -ioE \
  "\bnpm[[:space:]]+publish\b\
|\byarn[[:space:]]+publish\b\
|\bpnpm[[:space:]]+publish\b\
|\btwine[[:space:]]+upload\b\
|\bgem[[:space:]]+push\b\
|\bcargo[[:space:]]+publish\b" \
  | head -1)

if [ -n "$MATCHED" ]; then
  echo "Blocked: package publish command detected ('$MATCHED')." >&2
  echo "" >&2
  echo "Run manually if intentional:" >&2
  echo "" >&2
  echo "  $COMMAND" >&2
  exit 2
fi

exit 0
