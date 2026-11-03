#!/usr/bin/env bash
# Generic macOS notification wrapper around terminal-notifier.
# Standalone and source-agnostic: any script can call it.
#
# Usage:
#   notify.sh --title TITLE --message MESSAGE [--group GROUP]
#             [--execute COMMAND | --open URL]
#
#   --title    Banner title (required)
#   --message  Banner body (required)
#   --group    Notification group. A new banner with the same group replaces
#              the previous one (terminal-notifier -group behavior).
#   --execute  Shell command run when the banner is clicked
#   --open     URL opened when the banner is clicked
#              (--execute and --open are mutually exclusive)
#
# Exit codes: 0 sent, 2 usage error, 127 terminal-notifier not installed,
# otherwise terminal-notifier's own exit code.

set -uo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage: notify.sh --title TITLE --message MESSAGE [--group GROUP] [--execute COMMAND | --open URL]

  --title    Banner title (required)
  --message  Banner body (required)
  --group    Same group replaces the previous banner
  --execute  Shell command to run when the banner is clicked
  --open     URL to open when the banner is clicked (mutually exclusive with --execute)
USAGE
}

die_usage() {
  echo "notify.sh: $1" >&2
  usage
  exit 2
}

title="" message="" group="" execute="" open_url=""

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --title|--message|--group|--execute|--open)
      [ $# -ge 2 ] || die_usage "$1 requires a value"
      case "$1" in
        --title)   title="$2" ;;
        --message) message="$2" ;;
        --group)   group="$2" ;;
        --execute) execute="$2" ;;
        --open)    open_url="$2" ;;
      esac
      shift 2
      ;;
    *) die_usage "unknown argument: $1" ;;
  esac
done

[ -n "$title" ] || die_usage "--title is required"
[ -n "$message" ] || die_usage "--message is required"
if [ -n "$execute" ] && [ -n "$open_url" ]; then
  die_usage "--execute and --open are mutually exclusive"
fi

if ! command -v terminal-notifier >/dev/null 2>&1; then
  echo "notify.sh: terminal-notifier not found (brew install terminal-notifier)" >&2
  exit 127
fi

args=(-title "$title" -message "$message")
[ -n "$group" ] && args+=(-group "$group")
[ -n "$execute" ] && args+=(-execute "$execute")
[ -n "$open_url" ] && args+=(-open "$open_url")

exec terminal-notifier "${args[@]}"
