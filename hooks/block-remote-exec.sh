#!/bin/bash
# PreToolUse hook: blocks remote code execution patterns.
# Catches: curl/wget piped to sh/bash/python/etc., eval.
# Allows plain curl and wget (fetching without piping to shell).
# Exits 2 to block, 0 to allow.
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

MATCHED=$(echo "$COMMAND" | grep -ioE \
  "curl[[:space:]].*\|[[:space:]]*(sh|bash|zsh|python|node|perl|ruby)\
|wget[[:space:]].*\|[[:space:]]*(sh|bash|zsh|python|node|perl|ruby)\
|\beval[[:space:]]+" \
  | head -1)

if [ -n "$MATCHED" ]; then
  echo "Blocked: remote code execution pattern detected ('$MATCHED')." >&2
  echo "" >&2
  echo "Run manually if intentional:" >&2
  echo "" >&2
  echo "  $COMMAND" >&2
  exit 2
fi

exit 0
