#!/usr/bin/env bash
# Tests for capture/review-apply.sh and capture/import-batch.sh (adapters stubbed).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
C="$HERE/../capture"
T="$(mktemp -d)"; trap 'rm -r "$T"' EXIT
export CAPTURE_PENDING_DIR="$T/pending" CAPTURE_OUTBOX_DIR="$T/outbox" ADAPTER_LOG="$T/log"
cat > "$T/adapter" <<'S'
#!/usr/bin/env bash
printf 'new|%s|%s|%s\n' "$1" "$3" "$4" >> "$ADAPTER_LOG"
echo "https://github.com/$3/issues/42"
S
cat > "$T/cadapter" <<'S'
#!/usr/bin/env bash
printf 'comment|%s\n' "$*" >> "$ADAPTER_LOG"
echo "https://github.com/$3/issues/$1#issuecomment-1"
S
chmod +x "$T/adapter" "$T/cadapter"
export CAPTURE_ADAPTER="$T/adapter" CAPTURE_COMMENT_ADAPTER="$T/cadapter"
fail=0
ck() { if eval "$2"; then echo "ok: $1"; else echo "FAIL: $1"; fail=1; fi; }
mk() { printf '{"title":"%s","label":"%s","sealed":false,"what":"w","why":"y","where":"x","session":"s"}' "$1" "$2" > "$T/$1.json"; }

for t in One Two Three Four; do mk $t idea; "$C/pending.sh" add "$T/$t.json" >/dev/null; sleep 0.01; done
out="$("$C/review-apply.sh" "1,3" 2>&1)"; rc=$?
ck "review 1,3 exits 0" '[ $rc -eq 0 ]'
ck "files item 1 and 3" 'grep -c "^new|" "$ADAPTER_LOG" | grep -qx 2 && grep -q "^new|One|" "$ADAPTER_LOG" && grep -q "^new|Three|" "$ADAPTER_LOG"'
ck "does not file 2 or 4" '! grep -q -e "|Two|" -e "|Four|" "$ADAPTER_LOG"'
ck "queue emptied (rest dropped)" '[ -z "$("$C/pending.sh" list)" ]'

: > "$ADAPTER_LOG"; mk Five fix; "$C/pending.sh" add "$T/Five.json" >/dev/null
"$C/review-apply.sh" "1" --comment 1=57 >/dev/null
ck "approved match comments instead of new issue" 'grep -q "^comment|57 " "$ADAPTER_LOG" && ! grep -q "^new|" "$ADAPTER_LOG"'

mk Six idea; "$C/pending.sh" add "$T/Six.json" >/dev/null
ck "edit leaves queue and reports" '[ "$("$C/review-apply.sh" "e1 shorter")" = "edit 1 shorter" ] && [ "$("$C/pending.sh" list | wc -l | tr -d " ")" = 1 ]'

python3 -c '
import json
json.dump([{"title":"i%d"%i,"label":"idea","sealed":False,"what":"w","why":"y","where":"x","session":"s"} for i in range(40)], open("'"$T"'/items.json","w"))'
"$C/import-batch.sh" split "$T/items.json" "$T/b" > "$T/split.out"
ck "40 items -> 3 batches" '[ "$(wc -l < "$T/split.out" | tr -d " ")" = 3 ]'
ck "no batch over 15" '! awk "\$2>15{f=1} END{exit !f}" "$T/split.out"'
ck "batch sizes 15,15,10" '[ "$(cut -d" " -f2 "$T/split.out" | paste -sd, -)" = "15,15,10" ]'
rm "$CAPTURE_PENDING_DIR"/*.json
ck "queue adds batch" '[ "$("$C/import-batch.sh" queue "$T/b/batch-03.json")" = "queued 10" ] && [ "$("$C/pending.sh" list | wc -l | tr -d " ")" = 10 ]'
exit $fail
