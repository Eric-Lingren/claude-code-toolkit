#!/bin/bash
# UserPromptSubmit hook: show the cmux running row when a slash command is typed.
#
# Takes the leading /slug from the prompt and calls skill-status.sh start <slug>.
# Any other prompt calls skill-status.sh resume (waiting row goes back to running).
# Prints nothing (UserPromptSubmit stdout becomes model context). Always exits 0.

INPUT=$(cat)
# PreToolUse(Skill) payload: tool_input.skill. Otherwise UserPromptSubmit: leading /slug of .prompt.
SLUG=$(printf '%s' "$INPUT" | jq -r '.tool_input.skill // empty | strings' 2>/dev/null | head -n1)
if [ -z "$SLUG" ]; then
  PROMPT=$(printf '%s' "$INPUT" | jq -r '.prompt // ""' 2>/dev/null)
  SLUG=$(printf '%s' "$PROMPT" | sed -n '1s/^[[:space:]]*\/\([a-z][a-z0-9-]*\)\([[:space:]].*\)\{0,1\}$/\1/p')
fi

SCRIPT="$HOME/.claude-code-shared/scripts/cmux/skill-status.sh"
[ -f "$SCRIPT" ] || SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/scripts/cmux/skill-status.sh"
[ -f "$SCRIPT" ] || exit 0

# No slug: a plain reply. If a skill was waiting on the user, flip it back to running.
if [ -z "$SLUG" ]; then
  bash "$SCRIPT" resume >/dev/null 2>&1 </dev/null
  exit 0
fi

bash "$SCRIPT" start "$SLUG" >/dev/null 2>&1 </dev/null
exit 0
