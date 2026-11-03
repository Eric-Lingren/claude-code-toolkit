#!/usr/bin/env bash
# Capture outbox: queue captures that failed to file, retry them later.
# Usage:
#   outbox.sh enqueue <capture.json> <error message>
#   outbox.sh list
#   outbox.sh flush <filing-command> [args...]   (command gets the item path appended)
#   outbox.sh status
# Dir: $CAPTURE_OUTBOX_DIR (default ~/.dotfiles/local/capture-outbox)
set -u
DIR="${CAPTURE_OUTBOX_DIR:-$HOME/.dotfiles/local/capture-outbox}"
LAST="$DIR/_last-error"

items() { [ -d "$DIR" ] && find "$DIR" -maxdepth 1 -type f -name '*.json' | sort; }
mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"; }  # GNU first: BSD-style `stat -f` succeeds on Linux with filesystem info

cmd="${1:-}"; [ $# -gt 0 ] && shift
case "$cmd" in
  enqueue)
    src="${1:?capture json path required}"; msg="${2:-unknown error}"
    [ -f "$src" ] || { echo "no such file: $src" >&2; exit 1; }
    umask 077
    mkdir -p "$DIR"; chmod 700 "$DIR"
    dest="$DIR/$(date +%Y%m%d-%H%M%S)-$$-$RANDOM.json"
    cp "$src" "$dest"; chmod 600 "$dest"
    printf '%s' "$msg" > "$LAST"; chmod 600 "$LAST"
    osascript -e "display notification \"Capture failed: ${msg//\"/\\\"}. Saved to outbox.\" with title \"Capture\"" >/dev/null 2>&1 || true
    echo "queued: $(basename "$dest")"
    ;;
  list)
    items | while read -r f; do basename "$f"; done
    ;;
  flush)
    [ $# -gt 0 ] || { echo "filing command required" >&2; exit 2; }
    while read -r f; do
      [ -n "$f" ] || continue
      out="$("$@" "$f" 2>&1)"; rc=$?
      if [ $rc -eq 0 ]; then
        rm "$f"; echo "filed: $(basename "$f")"
      else
        prev=""; [ -f "$LAST" ] && prev="$(cat "$LAST")"
        if [ "$out" = "$prev" ]; then
          echo "same error as last attempt, skipping remaining: $out"
          exit 1
        fi
        umask 077; printf '%s' "$out" > "$LAST"
        echo "failed: $(basename "$f"): $out"
      fi
    done < <(items)
    if [ -z "$(items)" ] && [ -f "$LAST" ]; then rm "$LAST"; fi
    exit 0
    ;;
  status)
    first="$(items | head -1)"
    [ -n "$first" ] || exit 0
    n="$(items | wc -l | tr -d ' ')"
    age=$(( $(date +%s) - $(mtime "$first") ))
    last=""; [ -f "$LAST" ] && last="$(cat "$LAST")"
    echo "outbox: $n queued, oldest ${age}s, last error: ${last:-none}"
    ;;
  *) echo "usage: outbox.sh enqueue|list|flush|status" >&2; exit 2 ;;
esac
