#!/usr/bin/env bash
# cleanup-verification-temp.sh - delete to-seed verification temp files.
#
# Usage:
#   cleanup-verification-temp.sh [--help] <path> [<path> ...]
#
# See cleanup-verification-temp.py for full behavior and exit codes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/cleanup-verification-temp.py"

if [[ ! -f "${PY_SCRIPT}" ]]; then
    echo "ERROR: ${PY_SCRIPT} not found" >&2
    exit 1
fi

exec python3 "${PY_SCRIPT}" "$@"
