#!/usr/bin/env bash
# One-shot probe: can this (launchd) context reach the cmux socket?
# Appends a cmux_probe event (repo _system, pr 0, request-id cmux-probe) to the
# event log with data {reachable, rc, context, detail}. Always exits 0.
#
# Env: REVIEW_WATCHER_CMUX_BIN overrides the cmux binary (tests);
#      REVIEW_WATCHER_PROBE_TIMEOUT seconds (default 5);
#      REVIEW_WATCHER_PROBE_CONTEXT label stored in the event (default "launchd");
#      REVIEW_WATCHER_PROBE_SELF_REMOVE launchd label to remove after the probe, since
#        `launchctl submit` jobs are KeepAlive and would otherwise rerun forever;
#      REVIEW_WATCHER_LAUNCHCTL overrides launchctl (tests).
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$PATH"
HERE="$(cd "$(dirname "$0")" && pwd)"
CMUX="${REVIEW_WATCHER_CMUX_BIN:-/Applications/cmux.app/Contents/Resources/bin/cmux}"
SECS="${REVIEW_WATCHER_PROBE_TIMEOUT:-5}"
CONTEXT="${REVIEW_WATCHER_PROBE_CONTEXT:-launchd}"

if [ ! -x "$CMUX" ]; then
  rc=127; out="cmux binary not found or not executable: $CMUX"
else
  # perl alarm = portable timeout (cmux can hang on an unreachable socket)
  out=$(/usr/bin/perl -e 'alarm shift; exec @ARGV' "$SECS" "$CMUX" ping 2>&1); rc=$?
  [ "$rc" -eq 142 ] && out="timed out after ${SECS}s: $out"
fi
if [ "$rc" -eq 0 ]; then reachable=true; else reachable=false; fi

python3 "$HERE/probe-event.py" "$reachable" "$rc" "$CONTEXT" "$out"
echo "cmux probe: reachable=$reachable rc=$rc"
if [ -n "${REVIEW_WATCHER_PROBE_SELF_REMOVE:-}" ]; then
  "${REVIEW_WATCHER_LAUNCHCTL:-/bin/launchctl}" remove "$REVIEW_WATCHER_PROBE_SELF_REMOVE" >/dev/null 2>&1 || true
fi
exit 0
