#!/bin/bash
# Live queue view for the Review Watcher cmux workspace. See queue_view.py. Pass --once for one render.
exec python3 "$(dirname "${BASH_SOURCE[0]}")/queue_view.py" "$@"
