#!/usr/bin/env bash
# resolve-repo.sh — resolve the current repo's /pick configuration. Read-only.
#
# Resolves "Org/Repo" from the origin remote, looks up its repo-policy.json
# entry, and prints one JSON object to stdout:
#   {"repo": "...", "issue_tracker": "github|linear", "buckets": ["a","b"]}
# No issue_tracker: prints "no issue_tracker configured for <repo>" to stdout
# and exits 0 (clean exit, nothing else to do).
#
# Test hooks: PICK_REPO overrides the origin lookup; PICK_POLICY overrides the
# repo-policy.json path.
#
# Usage: resolve-repo.sh
set -euo pipefail

POLICY="${PICK_POLICY:-$(cd "$(dirname "$0")" && pwd)/../../resources/repo-policy.json}"

repo="${PICK_REPO:-}"
if [ -z "$repo" ]; then
  url=$(git remote get-url origin 2>/dev/null || true)
  repo=$(printf '%s' "$url" | sed -E 's#^(git@[^:]+:|https?://[^/]+/|ssh://[^/]+/)##; s#\.git$##')
fi
if [ -z "$repo" ]; then
  echo "no issue_tracker configured for (unknown repo: no origin remote)"
  exit 0
fi

REPO="$repo" POLICY="$POLICY" python3 - <<'PY'
import json, os, sys
repo, policy = os.environ["REPO"], os.environ["POLICY"]
entry = json.load(open(policy)).get(repo) or {}
tracker = entry.get("issue_tracker")
if not tracker:
    print(f"no issue_tracker configured for {repo}")
    sys.exit(0)
print(json.dumps({"repo": repo, "issue_tracker": tracker,
                  "buckets": list((entry.get("pick_buckets") or {}).keys())}))
PY
