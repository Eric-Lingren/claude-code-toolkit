#!/usr/bin/env bash
# Usage: task-filename.sh <slug>
# Outputs the task filename with current timestamp: YYYYMMDD-HHMM-{slug}.json
set -euo pipefail

if [ -z "${1:-}" ]; then
  echo "Usage: task-filename.sh <slug>" >&2
  exit 1
fi

"$(dirname "$0")/doc-filename.sh" "${1}" "json"
