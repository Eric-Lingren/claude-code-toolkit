#!/usr/bin/env bash
# launchd job that runs watcher.sh every CHECK_INTERVAL_MINUTES.
#
# Usage: launchd.sh install|uninstall|status|probe
#   install    render plist, bootstrap it, then run the cmux probe once under launchd
#   uninstall  bootout + remove plist (state dir and events.jsonl are never touched)
#   status     launchctl print for the job, plus plist and log paths
#   probe      run the cmux socket probe now, in the current context
#
# Env overrides (tests): REVIEW_WATCHER_LAUNCHCTL, REVIEW_WATCHER_LAUNCH_AGENTS_DIR,
# REVIEW_WATCHER_LABEL, REVIEW_WATCHER_HOME (state dir), REVIEW_WATCHER_SKIP_PROBE=1.
# The probe event is cmux_probe on the pseudo PR _system#0 (see events.py).
set -uo pipefail

SELF="$0"
while [ -L "$SELF" ]; do
  t="$(readlink "$SELF")"; case "$t" in /*) SELF="$t" ;; *) SELF="$(dirname "$SELF")/$t" ;; esac
done
HERE="$(cd "$(dirname "$SELF")" && pwd -P)"

LAUNCHCTL="${REVIEW_WATCHER_LAUNCHCTL:-/bin/launchctl}"
LABEL="${REVIEW_WATCHER_LABEL:-local.review-watcher}"
AGENTS_DIR="${REVIEW_WATCHER_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"
PLIST="$AGENTS_DIR/$LABEL.plist"
DOMAIN="gui/$(id -u)"
PROBE_LABEL="$LABEL.probe"

cfg() { python3 "$HERE/config.py" show "$1" | tr -d '"'; }

cmd_install() {
  local minutes state_dir
  minutes="$(cfg CHECK_INTERVAL_MINUTES)"; state_dir="$(cfg STATE_DIR)"
  mkdir -p "$AGENTS_DIR" "$state_dir" || return 1
  python3 "$HERE/render-plist.py" "$LABEL" "$HERE/watcher.sh" "$minutes" "$state_dir" > "$PLIST.tmp" || return 1
  if ! plutil -lint "$PLIST.tmp" >/dev/null; then echo "rendered plist is invalid" >&2; rm -f "$PLIST.tmp"; return 1; fi
  mv "$PLIST.tmp" "$PLIST"
  "$LAUNCHCTL" bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  "$LAUNCHCTL" bootstrap "$DOMAIN" "$PLIST" || { echo "launchctl bootstrap failed" >&2; return 1; }
  echo "installed $LABEL ($PLIST), every ${minutes}m, logs in $state_dir"
  if [ "${REVIEW_WATCHER_SKIP_PROBE:-}" != "1" ]; then
    # one-shot job in launchd context; its result lands in the event log
    "$LAUNCHCTL" remove "$PROBE_LABEL" >/dev/null 2>&1 || true
    local envargs=("REVIEW_WATCHER_PROBE_SELF_REMOVE=$PROBE_LABEL")
    [ -n "${REVIEW_WATCHER_LAUNCHCTL:-}" ] && envargs+=("REVIEW_WATCHER_LAUNCHCTL=$REVIEW_WATCHER_LAUNCHCTL")
    [ -n "${REVIEW_WATCHER_HOME:-}" ] && envargs+=("REVIEW_WATCHER_HOME=$REVIEW_WATCHER_HOME")
    [ -n "${REVIEW_WATCHER_CMUX_BIN:-}" ] && envargs+=("REVIEW_WATCHER_CMUX_BIN=$REVIEW_WATCHER_CMUX_BIN")
    "$LAUNCHCTL" submit -l "$PROBE_LABEL" -o "$state_dir/launchd.out.log" -e "$state_dir/launchd.err.log" -- \
      /usr/bin/env "${envargs[@]+"${envargs[@]}"}" /bin/bash "$HERE/cmux-probe.sh" \
      || echo "warning: could not submit cmux probe" >&2
    echo "cmux probe submitted ($PROBE_LABEL); result is a cmux_probe event in $(cfg EVENT_LOG_PATH)"
  fi
}

cmd_uninstall() {
  "$LAUNCHCTL" bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  "$LAUNCHCTL" remove "$PROBE_LABEL" >/dev/null 2>&1 || true
  rm -f "$PLIST"
  echo "uninstalled $LABEL (event log left intact: $(cfg EVENT_LOG_PATH))"
}

cmd_status() {
  echo "plist: $PLIST $([ -f "$PLIST" ] && echo present || echo missing)"
  echo "logs:  $(cfg STATE_DIR)/launchd.out.log, launchd.err.log"
  if "$LAUNCHCTL" print "$DOMAIN/$LABEL"; then return 0; fi
  echo "not loaded: $LABEL" >&2
  return 1
}

case "${1:-}" in
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  status) cmd_status ;;
  probe) REVIEW_WATCHER_PROBE_CONTEXT="${REVIEW_WATCHER_PROBE_CONTEXT:-manual}" bash "$HERE/cmux-probe.sh" ;;
  *) echo "usage: launchd.sh install|uninstall|status|probe" >&2; exit 2 ;;
esac
