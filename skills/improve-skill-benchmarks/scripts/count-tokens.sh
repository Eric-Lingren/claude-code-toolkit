#!/usr/bin/env bash
# Usage: count-tokens.sh <skill-name>
# Estimates token count (bytes / 4) across SKILL.md + resources/ + scripts/

set -euo pipefail

SKILL_NAME="${1:-}"
if [[ -z "$SKILL_NAME" ]]; then
  echo "Usage: $0 <skill-name>" >&2
  exit 1
fi

SKILL_DIR="$HOME/.dotfiles/claude-code-shared/skills/$SKILL_NAME"
if [[ ! -d "$SKILL_DIR" ]]; then
  echo "Skill directory not found: $SKILL_DIR" >&2
  exit 1
fi

total_bytes=0

add_bytes() {
  local path="$1"
  if [[ -f "$path" ]]; then
    total_bytes=$(( total_bytes + $(wc -c < "$path") ))
  elif [[ -d "$path" ]]; then
    while IFS= read -r -d '' f; do
      total_bytes=$(( total_bytes + $(wc -c < "$f") ))
    done < <(find "$path" -type f -print0)
  fi
}

add_bytes "$SKILL_DIR/SKILL.md"
add_bytes "$SKILL_DIR/resources"
add_bytes "$SKILL_DIR/scripts"

echo $(( total_bytes / 4 ))
