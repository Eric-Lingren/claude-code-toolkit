#!/bin/bash
# PreToolUse hook: blocks destructive git commands.
# Catches: push --force, reset --hard, clean -f, branch -D, checkout ., restore .,
# stash drop/clear, filter-branch, rebase -i.
# Exits 2 to block, 0 to allow.
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command')

# Strip git global options so `git -C <path> push --force` matches like
# `git push --force`. Covers -C/-c/--git-dir/--work-tree/--namespace (with a
# separate or =value arg, quoted or bare) and flag-only globals.
NORMALIZED=$(printf '%s' "$COMMAND" | perl -pe '
  1 while s/\bgit\s+(?:-[Cc]|--git-dir|--work-tree|--namespace)(?:\s+|=)(?:"[^"]*"|'"'"'[^'"'"']*'"'"'|\S+)/git/g
       || s/\bgit\s+(?:--no-pager|--paginate|-p|-P|--bare|--no-replace-objects|--literal-pathspecs|--no-optional-locks)(?=\s)/git/g;
')

MATCHED=$(echo "$NORMALIZED" | grep -ioE \
  "git push[[:space:]].*(-f|--force|--force-with-lease)\
|git reset[[:space:]]+--hard\
|git clean[[:space:]]+-[a-z]*f\
|git branch[[:space:]]+-D\
|git checkout[[:space:]]+(\.|--)\
|git restore[[:space:]]+\.\
|git stash[[:space:]]+(drop|clear)\
|git filter-branch\
|git rebase[[:space:]]+-i" \
  | head -1)

if [ -n "$MATCHED" ]; then
  echo "Blocked: destructive git command detected ('$MATCHED')." >&2
  echo "" >&2
  echo "Run manually if intentional:" >&2
  echo "" >&2
  echo "  $COMMAND" >&2
  exit 2
fi

exit 0
