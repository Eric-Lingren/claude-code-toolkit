#!/bin/bash
# PreToolUse hook: blocks destructive filesystem operations.
# Catches: rm -rf, rm *, chmod 777, chmod -R, dd, mkfs, fdisk, writes to /dev/.
# Allows normal rm (without -f), standard chmod, etc.
# Exits 2 to block, 0 to allow.
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')

# Strip harmless redirects (>/dev/null, 2>/dev/null, &>/dev/null, >/dev/stderr,
# >>/dev/tty, ...) before checking for /dev/ writes
SANITIZED=$(printf '%s' "$COMMAND" | perl -pe 's#[0-9&]?>>?\s*/dev/(?:null|stdout|stderr|tty)\b##g')

MATCHED=$(echo "$SANITIZED" | grep -ioE \
  "rm[[:space:]]+-[a-zA-Z]*f\
|rm[[:space:]]+\*\
|chmod[[:space:]]+777\
|chmod[[:space:]]+-R\
|dd[[:space:]]+if=\
|mkfs\b\
|fdisk\b\
|>[[:space:]]*/dev/" \
  | head -1)

if [ -n "$MATCHED" ]; then
  echo "Blocked: destructive filesystem op detected ('$MATCHED')." >&2
  echo "" >&2
  echo "Run manually if intentional:" >&2
  echo "" >&2
  echo "  $COMMAND" >&2
  exit 2
fi

exit 0
