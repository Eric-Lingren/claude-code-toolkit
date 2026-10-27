#!/usr/bin/env bash
# eval-report.sh — build an eval's report.html AND its plain-English summary.html.
#
# Usage: eval-report.sh <flow-dir>
#   e.g. eval-report.sh .claude/hillclimb/artifact-grounding-judge
#
# Runs the claude-api skill's report builder (full viewer if extracted, else
# the lite one) from the skill's own extracted directory, never a copy in the
# project, then eval-summary.py for the plain-English page. Always use this
# instead of calling the builder directly, so the summary is never skipped.
set -euo pipefail

FLOW="${1:?usage: eval-report.sh <flow-dir>}"
[ -d "$FLOW" ] || { echo "no such flow dir: $FLOW" >&2; exit 2; }
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Newest extracted claude-api skill. The CLI extracts it when /claude-api loads.
SKILL_ROOT="${CLAUDE_API_SKILL_DIR:-}"
if [ -z "$SKILL_ROOT" ]; then
  SKILL_ROOT=$(ls -td /private/tmp/claude-*/bundled-skills/*/*/claude-api 2>/dev/null | head -1 || true)
fi
R="$SKILL_ROOT/shared/evals/report"
B="$R/build-report.mjs"; [ -f "$B" ] || B="$R/build-report-lite.mjs"
if [ ! -f "$B" ]; then
  echo "report builder not found. Run any /claude-api command once to extract the skill, or set CLAUDE_API_SKILL_DIR." >&2
  exit 2
fi

RUNNER=node; command -v node >/dev/null || RUNNER=bun
"$RUNNER" "$B" "$FLOW"
python3 "$SCRIPTS/eval-summary.py" "$FLOW"
echo "Open: $FLOW/summary.html (plain English), $FLOW/report.html (every case)"
