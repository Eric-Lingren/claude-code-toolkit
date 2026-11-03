#!/usr/bin/env bash
# import-batch.sh — batching helper for the one-time Notion import (`capture import`).
#   import-batch.sh split <items.json> <out-dir> [size]
#       items.json is a JSON array of capture objects. Writes <out-dir>/batch-01.json ...
#       with at most `size` items each (default 15); prints "batch-NN.json <count>" per batch.
#   import-batch.sh queue <batch.json>
#       Adds each capture in the batch to the pending queue (pending.sh add), so the normal
#       review list and reply grammar apply. Prints the number queued.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd="${1:-}"; [ $# -gt 0 ] && shift
case "$cmd" in
  split)
    src="${1:?items.json required}"; out="${2:?out dir required}"; size="${3:-15}"
    [[ "$size" =~ ^[0-9]+$ ]] && [ "$size" -ge 1 ] || { echo "bad size: $size" >&2; exit 1; }
    mkdir -p "$out"
    python3 - "$src" "$out" "$size" <<'PY'
import json, sys
src, out, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
items = json.load(open(src))
if not isinstance(items, list):
    sys.exit("items.json must be a JSON array")
for i in range(0, len(items), size):
    chunk = items[i:i + size]
    name = "batch-%02d.json" % (i // size + 1)
    json.dump(chunk, open("%s/%s" % (out, name), "w"), indent=1)
    print(name, len(chunk))
PY
    ;;
  queue)
    batch="${1:?batch.json required}"
    tmp="$(mktemp -d)"; trap 'rm -r "$tmp"' EXIT
    python3 - "$batch" "$tmp" <<'PY' || exit 1
import json, sys
for i, c in enumerate(json.load(open(sys.argv[1]))):
    json.dump(c, open("%s/%03d.json" % (sys.argv[2], i), "w"))
PY
    n=0
    for f in "$tmp"/*.json; do
      "$HERE/pending.sh" add "$f" >/dev/null && n=$((n + 1)) || echo "skipped invalid item $(basename "$f" .json)" >&2
      sleep 0.01
    done
    echo "queued $n"
    ;;
  *) echo "usage: import-batch.sh split|queue" >&2; exit 2 ;;
esac
