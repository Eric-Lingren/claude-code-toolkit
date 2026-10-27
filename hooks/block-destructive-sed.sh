#!/bin/bash
# PreToolUse hook: blocks sed -i (in-place editing).
# Enforces using the Edit tool for file modifications instead.
# Exits 2 to block, 0 to allow.
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Match -i only within the same pipeline segment as sed, so `sed -n ...; grep -i`
# is not a false positive.
if echo "$COMMAND" | grep -qE '\bsed\b[^|;&]*[[:space:]]-[a-zA-Z]*i\b'; then
  echo "Destructive sed -i is blocked. Use the Edit tool to make in-place file changes instead." >&2
  exit 2
fi

exit 0
