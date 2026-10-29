#!/bin/bash
# skill-status.sh — show the running shared skill in the cmux sidebar status row.
#
# Usage: skill-status.sh start <slug>
#        skill-status.sh done  <slug>
#        skill-status.sh wait
#        skill-status.sh resume
#        skill-status.sh end
#
#   start <slug>  set status row "skill" to an hourglass + UPPERCASE slug, amber
#   done  <slug>  set status row "skill" to a check mark + UPPERCASE slug, green
#   wait          running skill paused for user input: speech bubble + slug, blue
#                 (no-op unless the row is currently running)
#   resume        user answered: back to the running hourglass
#                 (no-op unless the row is currently waiting)
#   end           clear the "skill" status row
#
# Colors use darker shades in light mode; theme is read once per call.
#
# start/done only act for shared skills: claude-code-shared/skills/<slug>/SKILL.md
# must exist and contain a skill-done marker, so plugin and built-in skills
# never get a row.
#
# Row state ("<running|waiting|done> <slug>") is kept per cmux workspace in
# /tmp/cmux-skill-status-<workspace>, so wait/resume know the slug and never
# overwrite a finished row.
#
# Silent no-op (exit 0, no output) when not inside cmux (no CMUX_WORKSPACE_ID),
# when the cmux binary is missing, or when the socket is unreachable. Every cmux
# call runs under a short timeout so a hook is never slowed or blocked.
#
# Exit codes: 0 done or silently skipped, 2 usage error.

set -u

KEY="skill"
CMUX_FALLBACK="/Applications/cmux.app/Contents/Resources/bin/cmux"
TIMEOUT_SECS=2
# Pill colors per macOS appearance (cmux follows the system theme). Bright
# shades wash out on the light sidebar, so light mode gets darker ones.
COLOR_RUNNING_DARK="#ff9500"
COLOR_WAITING_DARK="#0a84ff"
COLOR_DONE_DARK="#34c759"
COLOR_RUNNING_LIGHT="#b45309"
COLOR_WAITING_LIGHT="#1d4ed8"
COLOR_DONE_LIGHT="#15803d"

usage() {
  cat >&2 <<USAGE
Usage: skill-status.sh start <slug>
       skill-status.sh done  <slug>
       skill-status.sh wait
       skill-status.sh resume
       skill-status.sh end
USAGE
  exit 2
}

mode="${1:-}"
slug="${2:-}"

case "$mode" in
  start|done) [ -n "$slug" ] || usage ;;
  wait|resume|end) ;;
  *) usage ;;
esac

# Not inside cmux: nothing to do.
[ -n "${CMUX_WORKSPACE_ID:-}" ] || exit 0

STATE_FILE="/tmp/cmux-skill-status-$(printf '%s' "$CMUX_WORKSPACE_ID" | tr -cd 'A-Za-z0-9-')"

# Locate the cmux binary.
if command -v cmux >/dev/null 2>&1; then
  CMUX="$(command -v cmux)"
elif [ -x "$CMUX_FALLBACK" ]; then
  CMUX="$CMUX_FALLBACK"
else
  exit 0
fi

# Portable timeout (macOS has no coreutils timeout): perl forks the command,
# kills it after the deadline, and exits quietly either way.
run_cmux() {
  perl -e '
    my $t = shift;
    my $pid = fork();
    exit 1 unless defined $pid;
    if ($pid == 0) {
      open STDOUT, ">", "/dev/null";
      open STDERR, ">", "/dev/null";
      exec @ARGV or exit 127;
    }
    $SIG{ALRM} = sub { kill "KILL", $pid; waitpid($pid, 0); exit 124; };
    alarm $t;
    waitpid($pid, 0);
    exit($? >> 8);
  ' "$TIMEOUT_SECS" "$CMUX" "$@" >/dev/null 2>&1
}

if [ "$mode" = "end" ]; then
  run_cmux clear-status "$KEY" || true
  rm -f "$STATE_FILE"
  exit 0
fi

# AppleInterfaceStyle is "Dark" in dark mode and unset in light mode.
if [ "$(defaults read -g AppleInterfaceStyle 2>/dev/null)" = "Dark" ]; then
  COLOR_RUNNING="$COLOR_RUNNING_DARK"
  COLOR_WAITING="$COLOR_WAITING_DARK"
  COLOR_DONE="$COLOR_DONE_DARK"
else
  COLOR_RUNNING="$COLOR_RUNNING_LIGHT"
  COLOR_WAITING="$COLOR_WAITING_LIGHT"
  COLOR_DONE="$COLOR_DONE_LIGHT"
fi

# set_row <state> <slug>: paint the row for that state and record it.
set_row() {
  local upper
  upper="$(printf '%s' "$2" | tr '[:lower:]' '[:upper:]')"
  case "$1" in
    running) run_cmux set-status "$KEY" "⏳ $upper" --color "$COLOR_RUNNING" || true ;;
    waiting) run_cmux set-status "$KEY" "💬 $upper" --color "$COLOR_WAITING" || true ;;
    done)    run_cmux set-status "$KEY" "✅ $upper" --color "$COLOR_DONE" || true ;;
  esac
  printf '%s %s\n' "$1" "$2" > "$STATE_FILE" 2>/dev/null || true
}

# wait/resume flip only the matching prior state, reusing its slug.
if [ "$mode" = "wait" ] || [ "$mode" = "resume" ]; then
  [ -f "$STATE_FILE" ] || exit 0
  read -r cur_state cur_slug < "$STATE_FILE" || exit 0
  [ -n "${cur_slug:-}" ] || exit 0
  if [ "$mode" = "wait" ] && [ "$cur_state" = "running" ]; then
    set_row waiting "$cur_slug"
  elif [ "$mode" = "resume" ] && [ "$cur_state" = "waiting" ]; then
    set_row running "$cur_slug"
  fi
  exit 0
fi

# Only shared skills with a skill-done marker get a row. The slug pattern also
# rejects path traversal and plugin-style "plugin:skill" names.
case "$slug" in
  *[!a-z0-9-]*|-*) exit 0 ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_md="$script_dir/../../skills/$slug/SKILL.md"
[ -f "$skill_md" ] || exit 0
grep -Eq '<!-- skill-done:[[:space:]]*[^[:space:]>]+[[:space:]]*-->' "$skill_md" 2>/dev/null || exit 0

if [ "$mode" = "start" ]; then
  set_row running "$slug"
else
  set_row done "$slug"
fi
exit 0
