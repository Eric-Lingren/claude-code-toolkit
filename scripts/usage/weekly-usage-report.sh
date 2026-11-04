#!/bin/bash
# Weekly Claude Code usage report. Runs the benchmark across BOTH profiles
# (cco + cch) and writes a timestamped report. Designed to run via launchd
# (see launchd/com.ericlingren.cc-usage-report.plist), but is safe to run by hand.
#
# Output dir override: CC_USAGE_REPORT_DIR (default ~/.dotfiles/claude-code-shared/usage-reports)
set -uo pipefail

PY=/usr/bin/python3
SCRIPT="${CC_USAGE_BENCHMARK:-$HOME/.dotfiles/claude-code-shared/scripts/usage/cc-usage-benchmark.py}"
# Writes, commits and pushes change records for merges since the last run. The push only happens
# when the record commits are the only unpushed work on main; otherwise the output carries a
# "warning:" line, which lands in the CHANGE RECORDS section below. A failed push never fails the run.
CHANGE_RECORDS="${CC_CHANGE_RECORDS_WRITER:-$HOME/.dotfiles/claude-code-shared/scripts/attribution/commit-change-records.sh}"
# Drift / verified-regression detector. Alert-only (no git writes, no PRs); delivers via notify.sh.
DRIFT_ALERT="${CC_DRIFT_ALERT:-$HOME/.dotfiles/claude-code-shared/scripts/usage/drift-alert.py}"
OUTDIR="${CC_USAGE_REPORT_DIR:-$HOME/.dotfiles/claude-code-shared/usage-reports}"
mkdir -p "$OUTDIR"

# Change records first. A failure here must never stop the usage report.
CHANGE_RECORDS_TEXT="$("$CHANGE_RECORDS" 2>&1)" \
  || CHANGE_RECORDS_TEXT="$CHANGE_RECORDS_TEXT"$'\n'"(change record writer failed; continuing)"

# --latency and --tokens both need the per-skill spans (a full transcript scan).
# Share one scan between them via a per-user cache file, truncated at the start of
# every run so a stale result is never reused (an empty file is a cache miss).
export CC_USAGE_SPANS_CACHE="${TMPDIR:-/tmp}/cc-usage-spans-$(id -u).pkl"
: > "$CC_USAGE_SPANS_CACHE" 2>/dev/null || unset CC_USAGE_SPANS_CACHE

STAMP=$(date +%Y-%m-%d)
OUT="$OUTDIR/usage-$STAMP.txt"
DIGEST="$OUTDIR/digest-latest.txt"

# Digest first: it backfills the snapshot and prints the TL;DR delta header.
# Capture once, reuse for both the standalone nudge file and the report top.
DIGEST_TEXT="$("$PY" "$SCRIPT" --digest 2>&1)"

# Drift alerts. Must run after the digest: --digest is the only writer of the
# snapshot file the detector reads. A detector failure must never stop the usage
# report. Only stdout is alert text; stderr goes to a log so a stray warning is
# never injected into the digest as if it were an alert.
DRIFT_FAILED=""
DRIFT_LOG="$OUTDIR/drift-alert.log"
DRIFT_TEXT="$("$DRIFT_ALERT" --report-dir "$OUTDIR" 2>"$DRIFT_LOG")" \
  || { DRIFT_FAILED=1; DRIFT_TEXT="$DRIFT_TEXT"$'\n'"(drift detector failed; continuing; see $DRIFT_LOG)"; }

# Alert text rides along in the digest (and so the report top) when there is any.
[ -n "$DRIFT_TEXT" ] && [ -z "$DRIFT_FAILED" ] && DIGEST_TEXT="$DIGEST_TEXT"$'\n\n'"$DRIFT_TEXT"
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
  echo
  echo "================================================================"
  echo "CHANGE RECORDS"
  echo "================================================================"
  printf '%s\n' "$CHANGE_RECORDS_TEXT"
  echo
  echo "================================================================"
  echo "DRIFT ALERTS"
  echo "================================================================"
  if [ -n "$DRIFT_TEXT" ]; then printf '%s\n' "$DRIFT_TEXT"; else echo "none"; fi
  echo
  echo "================================================================"
  echo "SKILL OUTPUT TOKENS"
  echo "================================================================"
  "$PY" "$SCRIPT" --tokens || echo "(token trend failed; continuing)"
} > "$OUT" 2>&1

echo "wrote $OUT"
echo "wrote $DIGEST"
