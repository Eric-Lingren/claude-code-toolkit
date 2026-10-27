#!/usr/bin/env bash
# scaffold-evals.sh - Create or reset the runs directory for a skill
#
# Usage: scaffold-evals.sh <skill-name> [--reset]

set -euo pipefail

SKILL_NAME="${1:?Usage: scaffold-evals.sh <skill-name> [--reset]}"
RESET="${2:-}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMPROVE_SKILL_DIR="$(dirname "$SCRIPT_DIR")"
RUNS_DIR="$IMPROVE_SKILL_DIR/runs/$SKILL_NAME"
SKILL_DIR="$IMPROVE_SKILL_DIR/../$SKILL_NAME"

if [[ "$RESET" == "--reset" ]] && [[ -d "$RUNS_DIR" ]]; then
  rm -rf "$RUNS_DIR"
  echo "Deleted $RUNS_DIR"
fi

if [[ ! -d "$RUNS_DIR" ]]; then
  mkdir -p "$RUNS_DIR"
  echo "Created $RUNS_DIR"
fi

# Check which files exist
for f in eval.json learnings.md scores.json; do
  if [[ -f "$RUNS_DIR/$f" ]]; then
    echo "EXISTS: $RUNS_DIR/$f"
  else
    echo "MISSING: $RUNS_DIR/$f"
  fi
done

# Initialize scores.json if missing
if [[ ! -f "$RUNS_DIR/scores.json" ]]; then
  echo '{"runs": []}' > "$RUNS_DIR/scores.json"
  echo "Initialized $RUNS_DIR/scores.json"
fi

# Count tokens in SKILL.md (rough estimate: wc -w * 1.3)
if [[ -f "$SKILL_DIR/SKILL.md" ]]; then
  WORDS=$(wc -w < "$SKILL_DIR/SKILL.md")
  TOKENS=$(echo "$WORDS * 1.3" | bc | cut -d. -f1)
  echo "SKILL.md: ~${WORDS} words, ~${TOKENS} tokens"
fi
