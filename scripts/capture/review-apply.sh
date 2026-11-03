#!/usr/bin/env bash
# review-apply.sh — apply a `capture review` reply to the pending queue.
#   review-apply.sh <reply> [--target <org/repo>] [--comment <item>=<issue>]...
# Parses the reply with pending.sh review-reply (y | 1,3 | n | e2 <change> | ?2), then:
#   file  -> capture-file.sh file <item> --target T [--comment N]; item removed on success
#            (kept in the queue on failure; the driver already outboxed it)
#   drop  -> item removed
#   show  -> prints the item body, queue untouched
#   edit  -> prints "edit N <change>" for the skill to apply; queue untouched
# --comment 2=57 files item 2 as a "seen again" comment on #57 (an approved ≈ match).
# Env: CAPTURE_PENDING_DIR, plus the capture-file.sh test env.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PENDING="$HERE/pending.sh"
FILE="$HERE/capture-file.sh"
DIR="${CAPTURE_PENDING_DIR:-$HOME/.dotfiles/local/capture-pending}"

reply="${1:?reply required}"; shift
target="Eric-Lingren/dotfiles"; cm=()
while [ $# -gt 0 ]; do
  case "$1" in
    --target) target="${2:?--target needs a value}"; shift 2 ;;
    --comment) cm+=("${2:?--comment needs item=issue}"); shift 2 ;;
    *) echo "review-apply: unknown arg $1" >&2; exit 2 ;;
  esac
done

# Resolve every action before touching the queue: removals shift item numbers.
plan="$("$PENDING" review-reply "$reply")" || exit 1
rc=0
while read -r act n id change; do
  [ -n "$act" ] || continue
  f="$DIR/$id.json"
  case "$act" in
    file)
      args=(file "$f" --target "$target")
      for c in "${cm[@]+"${cm[@]}"}"; do [ "${c%%=*}" = "$n" ] && args+=(--comment "${c#*=}"); done
      if "$FILE" "${args[@]}"; then "$PENDING" remove "$id"; else rc=1; fi ;;
    drop) "$PENDING" remove "$id"; echo "dropped $n" ;;
    show) python3 "$HERE/validate-capture.py" "$f" ;;
    edit) echo "edit $n $change" ;;
  esac
done <<< "$plan"
exit $rc
