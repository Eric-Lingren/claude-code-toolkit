#!/bin/bash
# PreToolUse hook: blocks ES5 'var' keyword in JS/TS file writes.
# Only triggers when writing to .js/.ts/.tsx/.jsx files via redirect/heredoc.
# Enforces const/let per ES6+ conventions.
# Exits 2 to block, 0 to allow.
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')

# Detect writes to JS/TS files containing ES5 'var' keyword
IS_WRITE=$(echo "$COMMAND" | grep -qE '(>>?|tee\s|<<\s*[A-Z_'"'"']+)' && echo "true" || echo "false")
TARGETS_JS_TS=$(echo "$COMMAND" | grep -qE '\.(js|ts|tsx|jsx)' && echo "true" || echo "false")
HAS_VAR=$(echo "$COMMAND" | grep -qE '\bvar\s+[a-zA-Z_$]' && echo "true" || echo "false")

if [ "$IS_WRITE" = "true" ] && [ "$TARGETS_JS_TS" = "true" ] && [ "$HAS_VAR" = "true" ]; then
  echo "Blocked: 'var' keyword in write to JS/TS file. Use 'const' or 'let' per ES6+ conventions." >&2
  exit 2
fi

exit 0
