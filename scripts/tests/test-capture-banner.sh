#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$HERE/../../hooks/capture-banner.sh"
OB="$HERE/../capture/outbox.sh"
T="$(mktemp -d)"; trap 'rm -r "$T"' EXIT
mkdir -p "$T/bin"
printf '#!/bin/sh\nexit 0\n' > "$T/bin/osascript"; chmod +x "$T/bin/osascript"
export PATH="$T/bin:$PATH" CAPTURE_OUTBOX_DIR="$T/ob" CAPTURE_PENDING_DIR="$T/pd"
fail=0
ck() { if bash -c "$2"; then echo "ok: $1"; else echo "FAIL: $1"; fail=1; fi; }
export HOOK

ck "empty prints nothing" '[ -z "$(bash "$HOOK")" ]'

echo '{"a":1}' > "$T/c.json"
"$OB" enqueue "$T/c.json" "boom" >/dev/null
f="$(ls "$CAPTURE_OUTBOX_DIR"/*.json | head -1)"
touch -t 202001010000 "$f"
out="$(bash "$HOOK")"; export out
ck "outbox count" 'echo "$out" | grep -q "1 captures stuck in outbox"'
ck "outbox oldest age in days" 'echo "$out" | grep -qE "oldest [0-9]+d"'
ck "outbox last error" 'echo "$out" | grep -q "Last error: boom"'
ck "no pending line when pending empty" '! echo "$out" | grep -q pending'
rm "$f"

mkdir -p "$CAPTURE_PENDING_DIR"
echo '{"label":"idea","title":"t1"}' > "$CAPTURE_PENDING_DIR/20260101-000000-1-1.json"
out="$(bash "$HOOK")"; export out
ck "pending count and hint" 'echo "$out" | grep -q "1 pending, say capture review"'
ck "no outbox line when outbox empty" '! echo "$out" | grep -q outbox'
exit $fail
