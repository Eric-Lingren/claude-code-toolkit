#!/bin/bash
# UserPromptSubmit hook: nudge the assistant to invoke the capture skill when
# the prompt contains a specific capture phrase. Deliberately does NOT match
# the bare word "capture" (e.g. "capture a screenshot").
# Exits 0 always. Stdout becomes context for the turn.

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // ""' 2>/dev/null)
[ -z "$PROMPT" ] && exit 0

PATTERN='(^|[^[:alnum:]])(capture[[:space:]]+(idea|fix|bug|that)|file[[:space:]]+that[[:space:]]+issue|document[[:space:]]+that[[:space:]]+idea)([^[:alnum:]]|$)'
if echo "$PROMPT" | grep -iqE "$PATTERN"; then
  echo "[capture-phrase] The user used a capture phrase. Invoke the capture skill (Skill name 'capture') to file this as a GitHub issue."
fi
exit 0
