#!/usr/bin/env bash
# Usage: doc-filename.sh <slug> [ext]
# With ext:    YYYYMMDD-HHMM-{slug}.{ext}
# Without ext: YYYYMMDD-HHMM-{slug}
# Single source of truth for dated doc filename formatting across all skills.
set -euo pipefail

if [ -z "${1:-}" ]; then
  echo "Usage: doc-filename.sh <slug> [ext]" >&2
  exit 1
fi

if [ -n "${2:-}" ]; then
  echo "$(date +%Y%m%d-%H%M)-${1}.${2}"
else
  echo "$(date +%Y%m%d-%H%M)-${1}"
fi
