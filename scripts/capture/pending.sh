#!/usr/bin/env bash
# Capture pending queue: sweep candidates wait here for review; never auto-filed.
# Usage:
#   pending.sh add <capture.json>       validate, then queue
#   pending.sh list                     "N. [label] title (sealed)" per item
#   pending.sh review-reply <reply>     parse a reply, print one action per line:
#                                       file N <id> | drop N <id> | edit N <id> <change> | show N <id>
#   pending.sh show <N>                 print the validated body of item N
#   pending.sh remove <id>              delete an item after it was filed/dropped
#   pending.sh expire                   drop items unreviewed for 14 days
# Reply grammar: y (file all) | 1,3 (file those, drop rest) | n (drop all)
#                e2 <change> (edit item 2) | ?2 (show item 2)
# Dir: $CAPTURE_PENDING_DIR (default ~/.dotfiles/local/capture-pending)
set -u
DIR="${CAPTURE_PENDING_DIR:-$HOME/.dotfiles/local/capture-pending}"
HERE="$(cd "$(dirname "$0")" && pwd)"
VALIDATE="$HERE/validate-capture.py"
MAX_AGE=$((14 * 86400))

# capture-file.sh prepare leaves <item>.json.plan.json beside each item; those are not items.
items() { [ -d "$DIR" ] && find "$DIR" -maxdepth 1 -type f -name '*.json' ! -name '*.plan.json' | sort; }
mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"; }  # GNU first: BSD-style `stat -f` succeeds on Linux with filesystem info
nth() { items | sed -n "${1}p"; }
field() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get(sys.argv[2],""))' "$1" "$2"; }
idof() { basename "$1" .json; }

cmd="${1:-}"; [ $# -gt 0 ] && shift
case "$cmd" in
  add)
    src="${1:?capture json path required}"
    [ -f "$src" ] || { echo "no such file: $src" >&2; exit 1; }
    python3 "$VALIDATE" "$src" >/dev/null || { echo "rejected: capture failed validation" >&2; exit 1; }
    umask 077
    mkdir -p "$DIR"; chmod 700 "$DIR"
    dest="$DIR/$(date +%Y%m%d-%H%M%S)-$$-$RANDOM.json"
    cp "$src" "$dest"; chmod 600 "$dest"
    echo "queued: $(idof "$dest")"
    ;;
  list)
    n=0
    while read -r f; do
      [ -n "$f" ] || continue
      n=$((n + 1))
      line="$n. [$(field "$f" label)] $(field "$f" title)"
      [ "$(field "$f" sealed)" = "True" ] && line="$line (sealed)"
      echo "$line"
    done < <(items)
    ;;
  show)
    f="$(nth "${1:?item number required}")"
    [ -n "$f" ] || { echo "no item $1" >&2; exit 1; }
    python3 "$VALIDATE" "$f"
    ;;
  remove)
    f="$DIR/${1:?item id required}.json"
    [ -f "$f" ] || { echo "no such item: $1" >&2; exit 1; }
    rm "$f"
    if [ -f "$f.plan.json" ]; then rm "$f.plan.json"; fi
    ;;
  review-reply)
    reply="$(printf '%s' "${1:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    total="$(items | wc -l | tr -d ' ')"
    [ "$total" -gt 0 ] || { echo "nothing pending" >&2; exit 1; }
    emit() { echo "$1 $2 $(idof "$(nth "$2")")${3:+ $3}"; }
    lc="$(printf '%s' "$reply" | tr 'A-Z' 'a-z')"
    if [ "$lc" = y ]; then
      for i in $(seq 1 "$total"); do emit file "$i"; done
    elif [ "$lc" = n ]; then
      for i in $(seq 1 "$total"); do emit drop "$i"; done
    elif [[ "$lc" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
      IFS=, read -r -a keep <<< "$lc"
      for k in "${keep[@]}"; do
        [ "$k" -ge 1 ] && [ "$k" -le "$total" ] || { echo "no item $k" >&2; exit 1; }
      done
      for i in $(seq 1 "$total"); do
        act=drop
        for k in "${keep[@]}"; do [ "$k" -eq "$i" ] && act=file; done
        emit "$act" "$i"
      done
    elif [[ "$lc" =~ ^\?([0-9]+)$ ]]; then
      i="${BASH_REMATCH[1]}"
      [ "$i" -ge 1 ] && [ "$i" -le "$total" ] || { echo "no item $i" >&2; exit 1; }
      emit show "$i"
    elif [[ "$reply" =~ ^[eE]([0-9]+)[[:space:]]+(.+)$ ]]; then
      i="${BASH_REMATCH[1]}"
      [ "$i" -ge 1 ] && [ "$i" -le "$total" ] || { echo "no item $i" >&2; exit 1; }
      emit edit "$i" "${BASH_REMATCH[2]}"
    else
      echo "unrecognized reply: $reply" >&2; exit 1
    fi
    ;;
  expire)
    now="$(date +%s)"
    items | while read -r f; do
      [ -n "$f" ] || continue
      if [ $((now - $(mtime "$f"))) -gt "$MAX_AGE" ]; then
        rm "$f"
        if [ -f "$f.plan.json" ]; then rm "$f.plan.json"; fi
        echo "expired: $(idof "$f")"
      fi
    done
    ;;
  *) echo "usage: pending.sh add|list|show|remove|review-reply|expire" >&2; exit 2 ;;
esac
