#!/bin/bash
# SessionStart hook: surface stuck capture outbox items and pending captures.
# Prints nothing when both are empty. Never blocks (always exits 0).
# State dirs follow CAPTURE_OUTBOX_DIR / CAPTURE_PENDING_DIR (read by the scripts).
HERE="$(cd "$(dirname "$0")" && pwd)"
CAP="$HERE/../scripts/capture"

fmt_age() {
  local s=$1
  if [ "$s" -ge 86400 ]; then echo "$((s / 86400))d"
  elif [ "$s" -ge 3600 ]; then echo "$((s / 3600))h"
  elif [ "$s" -ge 60 ]; then echo "$((s / 60))m"
  else echo "${s}s"; fi
}

st="$("$CAP/outbox.sh" status 2>/dev/null)"
if [ -n "$st" ]; then
  n="$(echo "$st" | sed -n 's/^outbox: \([0-9]*\) queued.*/\1/p')"
  secs="$(echo "$st" | sed -n 's/.*oldest \([0-9]*\)s.*/\1/p')"
  err="$(echo "$st" | sed -n 's/.*last error: //p')"
  echo "⚠ ${n:-?} captures stuck in outbox (oldest $(fmt_age "${secs:-0}")). Last error: ${err:-none}"
fi

p="$("$CAP/pending.sh" list 2>/dev/null | grep -c .)"
if [ "${p:-0}" -gt 0 ]; then
  echo "💡 $p pending, say capture review"
fi
exit 0
