#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
P="$HERE/../capture/pending.sh"
T="$(mktemp -d)"; trap 'rm -r "$T"' EXIT
export CAPTURE_PENDING_DIR="$T/pending"
fail=0
ck() { if eval "$2"; then echo "ok: $1"; else echo "FAIL: $1"; fail=1; fi; }

mk() { # file title label sealed (sealed captures carry empty where/session)
  local w=somewhere s=s1
  [ "$4" = true ] && { w=""; s=""; }
  cat > "$1" <<J
{"title":"$2","label":"$3","sealed":$4,"what":"w","why":"y","where":"$w","session":"$s"}
J
}
mk "$T/a.json" "First idea" idea false
mk "$T/b.json" "Work fix" fix true
mk "$T/c.json" "Third" idea false
for x in a b c; do "$P" add "$T/$x.json" >/dev/null; sleep 0.01; done

out="$("$P" list)"
ck "list unsealed line" 'echo "$out" | sed -n 1p | grep -qx "1. \[idea\] First idea"'
ck "list sealed marker" 'echo "$out" | sed -n 2p | grep -qx "2. \[fix\] Work fix (sealed)"'

echo '{"title":"x"}' > "$T/bad.json"
ck "add rejects invalid" '! "$P" add "$T/bad.json" 2>/dev/null'
ck "invalid not queued" '[ "$("$P" list | wc -l | tr -d " ")" = 3 ]'

acts() { "$P" review-reply "$1" | awk '{printf "%s%s ", $1, $2}'; }
ck "y files all" '[ "$(acts y)" = "file1 file2 file3 " ]'
ck "n drops all" '[ "$(acts n)" = "drop1 drop2 drop3 " ]'
ck "1,3 files those, drops rest" '[ "$(acts 1,3)" = "file1 drop2 file3 " ]'
ck "e2 edit carries change" '[ "$("$P" review-reply "e2 make it shorter" | sed "s/^edit 2 [^ ]* //")" = "make it shorter" ]'
ck "e2 is edit action" '[ "$(acts "e2 x")" = "edit2 " ]'
ck "?2 shows" '[ "$(acts "?2")" = "show2 " ]'
ck "show body" '"$P" show 2 | grep -q "w"'
ck "bad reply fails" '! "$P" review-reply "zzz" 2>/dev/null'
ck "out of range fails" '! "$P" review-reply "9" 2>/dev/null'

old="$(ls "$CAPTURE_PENDING_DIR"/*.json | head -1)"
touch -t 202001010000 "$old"
"$P" expire >/dev/null
ck "expire removes only old" '[ ! -f "$old" ] && [ "$(ls "$CAPTURE_PENDING_DIR"/*.json | wc -l | tr -d " ")" = 2 ]'
id="$(basename "$(ls "$CAPTURE_PENDING_DIR"/*.json | head -1)" .json)"
"$P" remove "$id"
ck "remove deletes" '[ "$("$P" list | wc -l | tr -d " ")" = 1 ]'

# capture-file.sh prepare writes <item>.json.plan.json beside each queued item.
last="$(ls "$CAPTURE_PENDING_DIR"/*.json | head -1)"
echo '{"label":"idea"}' > "$last.plan.json"
ck "plan files not listed as items" '[ "$("$P" list | wc -l | tr -d " ")" = 1 ]'
ck "plan files not counted by review-reply" '[ "$(acts y)" = "file1 " ]'
"$P" remove "$(basename "$last" .json)"
ck "remove deletes plan file" '[ ! -f "$last.plan.json" ]'
mk "$T/d.json" "Stale" idea false; "$P" add "$T/d.json" >/dev/null
stale="$(ls "$CAPTURE_PENDING_DIR"/*.json | head -1)"; echo '{}' > "$stale.plan.json"
touch -t 202001010000 "$stale" "$stale.plan.json"
"$P" expire >/dev/null
ck "expire deletes plan file" '[ ! -f "$stale.plan.json" ]'
exit $fail
