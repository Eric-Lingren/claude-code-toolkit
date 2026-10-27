#!/bin/bash
# PreToolUse hook: block edits/writes while on a trunk branch (main/master/trunk)
# in a project repo, so work does not accidentally land on trunk.
#
# Exempt: the dotfiles repo (trunk edits are normal there) and any repo whose
# root matches a substring in CLAUDE_TRUNK_OK (colon-separated).
# Disable entirely: export CLAUDE_BRANCH_GUARD=0
# Bypass one edit: set CLAUDE_BRANCH_GUARD=0 for that command.

[ "${CLAUDE_BRANCH_GUARD:-1}" = "0" ] && exit 0

INPUT=$(cat)
FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)
[ -z "$FILE" ] && exit 0

dir=$(dirname "$FILE")
[ -d "$dir" ] || exit 0

branch=$(git -C "$dir" branch --show-current 2>/dev/null)
case "$branch" in
  main|master|trunk) ;;     # on trunk: keep checking
  *) exit 0 ;;              # not on trunk (or not a repo): allow
esac

root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)

# Exemptions.
case "$root" in *dotfiles*) exit 0 ;; esac

# Doc scaffolding (seeds, tasks, prd, archive, adr, prototype) is edited on
# trunk by design, so exempt anything under a docs/ directory.
case "$FILE" in */docs/*|docs/*) exit 0 ;; esac
IFS=':' read -ra OK <<< "${CLAUDE_TRUNK_OK:-}"
for pat in "${OK[@]}"; do
  [ -n "$pat" ] && case "$root" in *"$pat"*) exit 0 ;; esac
done

echo "Branch guard: on '$branch' in $(basename "$root"). Create a feature branch before editing (git checkout -b feat/...). Bypass: CLAUDE_BRANCH_GUARD=0, or add the repo to CLAUDE_TRUNK_OK." >&2
exit 2
