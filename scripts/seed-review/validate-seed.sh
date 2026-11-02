#!/usr/bin/env bash
# validate-seed.sh - validate a to-seed draft against contracts/seed-schema.json.
#
# Usage:
#   validate-seed.sh [--help] <seed-path> [--schema <path>]
#
# See validate-seed.py for full behavior and exit codes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/validate-seed.py"

if [[ ! -f "${PY_SCRIPT}" ]]; then
    echo "ERROR: ${PY_SCRIPT} not found" >&2
    exit 1
fi

exec python3 "${PY_SCRIPT}" "$@"
