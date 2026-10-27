#!/usr/bin/env bash
# validate-schema.sh — validate JSON schema files and their embedded examples,
# or validate a data instance against a named schema.
#
# Usage:
#   validate-schema.sh [--help]
#   validate-schema.sh [--self-test] [schema-file ...]
#   validate-schema.sh --instance <schema-file> <data-file>
#
# Modes:
#   (no args)                        Validate examples[] in all contracts/*.json
#   schema-file ...                  Validate examples[] in the given schema files
#   --self-test                      Run the cross-file $ref mechanism self-test
#   --instance <schema-file> <data>  Validate data-file against schema-file
#
# Exit codes:
#   0  All checks passed
#   1  One or more checks failed, or invalid invocation

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTRACTS_DIR="$(cd "${SCRIPT_DIR}/../../contracts" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/validate-schema.py"

# --help / -h
if [[ $# -ge 1 && ( "$1" == "--help" || "$1" == "-h" ) ]]; then
    sed -n '2,/^$/p' "$0" | grep '^#' | sed 's/^# \?//'
    exit 0
fi

# Guard against the old 2-arg positional API: validate-schema.sh <schema-path> <instance-path>
# Use --instance <schema-file> <data-file> instead.
if [[ $# -eq 2 && -f "$1" && "$1" != --* && "$2" != --* ]]; then
    echo "ERROR: validate-schema.sh API changed." >&2
    echo "  Old form: validate-schema.sh <schema-path> <instance-path>" >&2
    echo "  New form: validate-schema.sh --instance <schema-file> <data-file>" >&2
    echo "  Run: validate-schema.sh --help for full usage." >&2
    exit 1
fi

# Unknown flags: any --flag not in the known set
for arg in "$@"; do
    case "$arg" in
        --help|-h|--self-test|--instance) ;;
        --*)
            echo "ERROR: unknown flag: $arg" >&2
            echo "  Run: validate-schema.sh --help for usage." >&2
            exit 1
            ;;
    esac
done

if [[ ! -f "${PY_SCRIPT}" ]]; then
    echo "ERROR: ${PY_SCRIPT} not found" >&2
    exit 1
fi

if ! python3 -c "import jsonschema" 2>/dev/null; then
    echo "ERROR: jsonschema not installed. Run: pip install 'jsonschema[format]'" >&2
    exit 1
fi

exec python3 "${PY_SCRIPT}" "${CONTRACTS_DIR}" "$@"
