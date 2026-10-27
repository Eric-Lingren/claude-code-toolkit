#!/bin/bash
# PreToolUse hook: blocks destructive SQL operations.
# Catches: DROP (table, database, schema, view, etc.), TRUNCATE, DELETE FROM.
# Exits 2 to block, 0 to allow.
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')

# Match any destructive SQL keyword (case-insensitive)
MATCHED=$(echo "$COMMAND" | grep -ioE \
  "drop[[:space:]]+(table|database|schema|view|function|procedure|trigger|index|extension|type|role|user|column|constraint)\
|truncate[[:space:]]+[a-zA-Z\`\"]\
|delete[[:space:]]+from" \
  | head -1)

if [ -n "$MATCHED" ]; then
  echo "Blocked: destructive SQL '$(echo "$MATCHED" | tr '[:lower:]' '[:upper:]')' detected." >&2
  echo "" >&2
  echo "Run manually if intentional:" >&2
  echo "" >&2
  echo "$COMMAND" >&2
  exit 2
fi

exit 0
