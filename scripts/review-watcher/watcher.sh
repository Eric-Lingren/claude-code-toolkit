#!/usr/bin/env bash
# One tick of the PR review watcher. See watcher.py for the tick order.
#
# Usage: watcher.sh [--once] [--dry-run]
# Silent no-op outside weekdays 8:50am-6pm PT (ACTIVE_WINDOW in config.py).

HERE="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$HERE/watcher.py" "$@"
