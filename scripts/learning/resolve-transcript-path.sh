#!/usr/bin/env bash
# Print the absolute path of the current session transcript, or "null" if it can't be resolved.
set -euo pipefail

config_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
session_id="${CLAUDE_CODE_SESSION_ID:-}"
cwd="${1:-$(pwd)}"

if [[ -z "$session_id" ]]; then
  echo "null"
  exit 0
fi

encoded_cwd=$(printf '%s' "$cwd" | tr './' '--')
path="${config_dir}/projects/${encoded_cwd}/${session_id}.jsonl"

if [[ -f "$path" ]]; then
  echo "$path"
  exit 0
fi

# Session IDs are unique, so fall back to a glob in case cwd encoding differs.
for match in "${config_dir}"/projects/*/"${session_id}.jsonl"; do
  if [[ -f "$match" ]]; then
    echo "$match"
    exit 0
  fi
done

echo "null"
