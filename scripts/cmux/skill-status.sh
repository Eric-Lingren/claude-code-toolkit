#!/bin/bash
# skill-status.sh — show the running shared skill in the cmux sidebar status row.
#
# Usage: skill-status.sh start <slug>
#        skill-status.sh done  <slug>
#        skill-status.sh end
#
#   start <slug>  set status row "skill" to an hourglass + UPPERCASE slug
#   done  <slug>  set status row "skill" to a check mark + UPPERCASE slug
#   end           clear the "skill" status row
#
# start/done only act for shared skills: claude-code-shared/skills/<slug>/SKILL.md
# must exist and contain a skill-done marker, so plugin and built-in skills
# never get a row.
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

usage() {
  cat >&2 <<USAGE
Usage: skill-status.sh start <slug>
       skill-status.sh done  <slug>
       skill-status.sh end
USAGE
  exit 2
}

mode="${1:-}"
slug="${2:-}"

case "$mode" in
  start|done) [ -n "$slug" ] || usage ;;
  end) ;;
  *) usage ;;
esac

# Not inside cmux: nothing to do.
[ -n "${CMUX_WORKSPACE_ID:-}" ] || exit 0

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

upper="$(printf '%s' "$slug" | tr '[:lower:]' '[:upper:]')"

if [ "$mode" = "start" ]; then
  run_cmux set-status "$KEY" "⏳ $upper" || true
else
  run_cmux set-status "$KEY" "✓ $upper" || true
fi
exit 0
