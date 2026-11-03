#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
H="$HERE/../../hooks/capture-phrase.sh"
fail=0
run() { jq -n --arg p "$1" '{prompt:$p}' | bash "$H"; }

pos=("capture idea" "capture fix" "capture bug" "capture that" "file that issue" "document that idea"
  "please capture idea: dark mode" "Can you capture that?" "CAPTURE BUG in login" "File That Issue" "Document That Idea now" "capture   fix")
neg=("capture a screenshot" "capture the output" "capture" "Capture the logs" "document the idea" "file the issue" "captured that" "recapture ideas" "")
for p in "${pos[@]}"; do
  out="$(run "$p")"
  if echo "$out" | grep -q "capture skill"; then echo "ok: fires: $p"; else echo "FAIL: fires: $p"; fail=1; fi
done
for p in "${neg[@]}"; do
  out="$(run "$p")"
  if [ -z "$out" ]; then echo "ok: silent: $p"; else echo "FAIL: silent: $p"; fail=1; fi
done
exit $fail
