#!/bin/bash
# Weekly Claude Code usage report. Runs the benchmark across BOTH profiles
# (cco + cch) and writes a timestamped report. Designed to run via launchd
# (see launchd/com.ericlingren.cc-usage-report.plist), but is safe to run by hand.
#
# Output dir override: CC_USAGE_REPORT_DIR (default ~/.dotfiles/claude-code-shared/usage-reports)
set -uo pipefail

PY=/usr/bin/python3
SCRIPT="$HOME/.dotfiles/claude-code-shared/scripts/usage/cc-usage-benchmark.py"
OUTDIR="${CC_USAGE_REPORT_DIR:-$HOME/.dotfiles/claude-code-shared/usage-reports}"
mkdir -p "$OUTDIR"

STAMP=$(date +%Y-%m-%d)
OUT="$OUTDIR/usage-$STAMP.txt"
DIGEST="$OUTDIR/digest-latest.txt"

# Digest first: it backfills the snapshot and prints the TL;DR delta header.
# Capture once, reuse for both the standalone nudge file and the report top.
DIGEST_TEXT="$("$PY" "$SCRIPT" --digest 2>&1)"
printf '%s\n' "$DIGEST_TEXT" > "$DIGEST"

{
  printf '%s\n' "$DIGEST_TEXT"
  echo
  echo "Claude Code usage report — $STAMP"
  echo "================================================================"
  echo
  "$PY" "$SCRIPT"
  echo
  echo "================================================================"
  echo "WEEKLY TREND"
  echo "================================================================"
  "$PY" "$SCRIPT" --trend
  echo
  echo "================================================================"
  echo "TIER ADHERENCE"
  echo "================================================================"
  "$PY" "$SCRIPT" --adherence
  echo
  echo "================================================================"
  echo "SKILL LATENCY"
  echo "================================================================"
  "$PY" "$SCRIPT" --latency --min-n 3
} > "$OUT" 2>&1

echo "wrote $OUT"
echo "wrote $DIGEST"
