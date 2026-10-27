#!/usr/bin/env bash
# filter-session-transcript.sh — clean a Claude Code session JSONL for persona use.
#
# Usage:
#   filter-session-transcript.sh [--help] <output-path>
#
# Environment:
#   transcript_path         Explicit path to session JSONL (overrides auto-resolve)
#   CLAUDE_CODE_SESSION_ID  Session ID for auto-resolve
#   CLAUDE_CONFIG_DIR       Claude config dir (default: ~/.claude)
#
# Exit codes:
#   0  Success
#   1  Error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/filter-session-transcript.py"

if [[ ! -f "${PY_SCRIPT}" ]]; then
    echo "ERROR: ${PY_SCRIPT} not found" >&2
    exit 1
fi

exec python3 "${PY_SCRIPT}" "$@"
