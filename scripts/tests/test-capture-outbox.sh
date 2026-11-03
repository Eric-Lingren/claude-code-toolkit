#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
OB="$HERE/../capture/outbox.sh"
T="$(mktemp -d)"; trap 'rm -r "$T"' EXIT
mkdir -p "$T/bin"
cat > "$T/bin/osascript" <<'S'
#!/bin/sh
echo "$@" >> "$OSA_LOG"
S
chmod +x "$T/bin/osascript"
export PATH="$T/bin:$PATH" OSA_LOG="$T/osa.log" CAPTURE_OUTBOX_DIR="$T/ob"
fail=0
ck() { if eval "$2"; then echo "ok: $1"; else echo "FAIL: $1"; fail=1; fi; }

echo '{"a":1}' > "$T/c.json"
ck "status empty prints nothing" '[ -z "$("$OB" status)" ]'

"$OB" enqueue "$T/c.json" "boom" >/dev/null
f="$(ls "$CAPTURE_OUTBOX_DIR"/*.json | head -1)"
mode="$(stat -c %a "$f" 2>/dev/null || stat -f %Lp "$f")"
ck "enqueue mode 600" '[ "$mode" = 600 ]'
ck "enqueue json content" 'grep -q "\"a\":1" "$f"'
ck "_last-error updated" '[ "$(cat "$CAPTURE_OUTBOX_DIR/_last-error")" = boom ]'
ck "osascript got failure text" 'grep -q "Capture failed: boom. Saved to outbox." "$OSA_LOG"'

s="$("$OB" status)"
ck "status count/age/error" 'echo "$s" | grep -q "1 queued" && echo "$s" | grep -q oldest && echo "$s" | grep -q boom'

printf '#!/bin/sh\nexit 0\n' > "$T/ok.sh"; chmod +x "$T/ok.sh"
out="$("$OB" flush "$T/ok.sh")"
ck "flush notice per item" '[ "$(echo "$out" | grep -c "^filed:")" = 1 ]'
ck "flush deleted on success" '[ -z "$("$OB" list)" ]'

for i in 1 2 3; do "$OB" enqueue "$T/c.json" "boom" >/dev/null; done
printf '#!/bin/sh\necho boom\nexit 1\n' > "$T/bad.sh"; chmod +x "$T/bad.sh"
out="$("$OB" flush "$T/bad.sh")"
ck "flush stops on repeat error, leaves queue" '[ "$("$OB" list | wc -l | tr -d " ")" = 3 ] && echo "$out" | grep -q skipping'
ck "error shown once" '[ "$(echo "$out" | grep -c boom)" = 1 ]'
exit $fail
