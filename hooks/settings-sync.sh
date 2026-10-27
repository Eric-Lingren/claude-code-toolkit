#!/bin/bash
# SessionStart + PostToolUse hook: rebuild each account's settings.json from the
# tracked base (claude-code-shared/settings.json) plus the private overlay
# (local/claude-settings.json). See scripts/profile/sync-settings.py.
#
# SessionStart: always sync, so runtime edits get captured and base changes land.
# PostToolUse: sync only when the edited file is one of the two sources.
# Never blocks: sync problems print to stderr and exit 0.

input="$(cat)"
case "$input" in
  *'"SessionStart"'*) ;;
  *claude-code-shared/settings.json*|*local/claude-settings.json*) ;;
  *) exit 0 ;;
esac

python3 "$HOME/.claude-code-shared/scripts/profile/sync-settings.py" || true
exit 0
