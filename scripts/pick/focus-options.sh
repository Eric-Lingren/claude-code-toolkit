#!/usr/bin/env bash
# focus-options.sh — build the /pick "Focus?" menu options live. Read-only.
#
# Usage: focus-options.sh <Org/Repo> <bucket>
# Prints JSON: {"ask": bool, "options": ["<project>", ..., "No focus"]}
#   ask=false (options empty) when the bucket is not flagged focus:true in
#   repo-policy.json (my-current-sprint, every SpawnedSapien bucket): never prompt.
#   ask=true: options are the distinct projects on the user's own current
#   sprint tickets (the repo's my-current-sprint bucket, fetched via
#   pick-fetch-linear.sh --all-states, so in-flight tickets count), then "No focus". The caller adds free-text "Other".
# Nothing is written to disk. Test hooks: PICK_POLICY, LINEAR_ISSUES_FIXTURE
# (fixture used for the my-current-sprint fetch).
set -euo pipefail
repo="${1:?usage: focus-options.sh <Org/Repo> <bucket>}"
bucket="${2:?usage: focus-options.sh <Org/Repo> <bucket>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
POLICY="${PICK_POLICY:-$HERE/../../resources/repo-policy.json}"

flag=$(REPO="$repo" BUCKET="$bucket" POLICY="$POLICY" python3 -c '
import json, os
b = ((json.load(open(os.environ["POLICY"])).get(os.environ["REPO"]) or {}).get("pick_buckets") or {}).get(os.environ["BUCKET"]) or {}
print("1" if b.get("focus") else "0")')
if [ "$flag" != "1" ]; then
  echo '{"ask": false, "options": []}'
  exit 0
fi
bash "$HERE/pick-fetch-linear.sh" "$repo" my-current-sprint --all-states | python3 -c '
import json, sys
d = json.load(sys.stdin)
ps = sorted({c["project"] for c in d["candidates"] if c.get("project")})
print(json.dumps({"ask": True, "options": ps + ["No focus"]}))'
