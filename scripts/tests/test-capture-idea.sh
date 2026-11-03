#!/usr/bin/env bash
# Tests: SessionEnd sweep hook (stubbed spawn), sweep never files, wiring.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SHARED="$HERE/../.."
HOOK="$SHARED/hooks/capture-idea-session-end.sh"
PEND="$SHARED/scripts/capture/pending.sh"
T="$(mktemp -d)"; trap 'rm -r "$T"' EXIT
fail=0
ck() { if bash -c "$2"; then echo "ok: $1"; else echo "FAIL: $1"; fail=1; fi; }

mkdir -p "$T/bin"
# any filing attempt is recorded here
for b in gh; do
  printf '#!/bin/sh\necho "$0 $*" >> "%s/filed.log"\nexit 0\n' "$T" > "$T/bin/$b"; chmod +x "$T/bin/$b"
done
export PATH="$T/bin:$PATH" CAPTURE_PENDING_DIR="$T/pd"
AG="$SHARED/agents/learning/capture-idea.md"
export PEND SHARED HOOK T AG

cat > "$T/stub.sh" <<EOF
#!/bin/bash
echo "\$CAPTURE_IDEA_TRANSCRIPT|\$CAPTURE_IDEA_SESSION" > "$T/stub.args"
cat > "$T/cand.json" <<'J'
{"title":"Swept idea","label":"idea","sealed":true,"what":"Passing mention.","why":"Said at session end."}
J
bash "$PEND" add "$T/cand.json"
EOF
export CAPTURE_IDEA_SPAWN_CMD="bash $T/stub.sh" CAPTURE_IDEA_SYNC=1

echo '{"transcript_path":"/x/t.jsonl","session_id":"s1","cwd":"'"$T"'"}' | bash "$HOOK"
ck "hook exits 0" 'true'
ck "stub got transcript and session" '[ "$(cat "$T/stub.args")" = "/x/t.jsonl|s1" ]'
ck "candidate written to pending dir" '[ "$(ls "$CAPTURE_PENDING_DIR"/*.json | wc -l | tr -d " ")" = 1 ]'
ck "pending list shows it" 'bash "$PEND" list | grep -q "Swept idea (sealed)"'
ck "sweep never invoked filing adapter" '[ ! -e "$T/filed.log" ]'

rm "$CAPTURE_PENDING_DIR"/*.json
echo '{"session_id":"s2"}' | bash "$HOOK"
ck "no transcript: no spawn" '[ -z "$(ls "$CAPTURE_PENDING_DIR" 2>/dev/null)" ]'

echo '{"transcript_path":"/x/t.jsonl"}' | CAPTURE_IDEA_ACTIVE=1 bash "$HOOK"
ck "recursion guard: no spawn" '[ -z "$(ls "$CAPTURE_PENDING_DIR" 2>/dev/null)" ]'

# non-sync path returns without waiting
SLOW="$T/slow.sh"; printf '#!/bin/bash\nsleep 3\ntouch "%s/slow.done"\n' "$T" > "$SLOW"
s=$(date +%s)
echo '{"transcript_path":"/x/t.jsonl"}' | CAPTURE_IDEA_SYNC= CAPTURE_IDEA_SPAWN_CMD="bash $SLOW" bash "$HOOK"
e=$(date +%s)
ck "background spawn does not block" '[ $((e - s)) -lt 2 ]'

# static: sweep agent and hook never reference filing paths
AG="$SHARED/agents/learning/capture-idea.md"
ck "agent declares sonnet" 'grep -q "^model: sonnet" "$AG"'
ck "agent has sealed rules" 'grep -q "MUST be .sealed: true." "$AG" && grep -q "wall-check.sh --mode capture" "$AG"'
ck "agent forbids filing" 'grep -q "NEVER file" "$AG"'
ck "hook does not reference filing adapter" '! grep -qE "capture-file|export-tasks|gh issue" "$HOOK"'
ck "registry has capture-idea" 'python3 -c "import json,sys;r=json.load(open(\"$SHARED/agents/registry.json\"));sys.exit(0 if any(a[\"name\"]==\"capture-idea\" and a[\"model\"]==\"sonnet\" for a in r[\"agents\"]) else 1)"'
ck "settings.json SessionEnd wires hook" 'python3 -c "import json,sys;s=json.load(open(\"$SHARED/settings.json\"));sys.exit(0 if any(\"capture-idea-session-end.sh\" in h[\"command\"] for g in s[\"hooks\"][\"SessionEnd\"] for h in g[\"hooks\"]) else 1)"'
ck "learning-capture spawns sweep in background" 'grep -q "subagent_type:" "$SHARED/resources/learning-capture.md" && grep -A3 "2b" "$SHARED/resources/learning-capture.md" | grep -q capture-idea && grep -q "run_in_background: true" "$SHARED/resources/learning-capture.md"'
sleep 3.2
exit $fail
