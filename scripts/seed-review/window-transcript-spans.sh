#!/usr/bin/env bash
# window-transcript-spans.sh — build a judge evidence pack from a cleaned transcript.
#
# Usage:
#   window-transcript-spans.sh [--help] <cleaned-transcript> <refutations-json> <output-pack>
#
# See window-transcript-spans.py for full behavior and exit codes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/window-transcript-spans.py"

if [[ ! -f "${PY_SCRIPT}" ]]; then
    echo "ERROR: ${PY_SCRIPT} not found" >&2
    exit 1
fi

exec python3 "${PY_SCRIPT}" "$@"
