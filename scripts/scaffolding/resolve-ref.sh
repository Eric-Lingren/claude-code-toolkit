#!/usr/bin/env bash
# resolve-ref.sh — resolve a docs/ artifact basename to its active path or archived content.
# Usage: resolve-ref.sh <basename>
#
# Lookup order:
#   1. Active file: find docs/ -name <basename> (recursive). Print path to stdout. Exit 0.
#   2. Archive:     grep docs/archive/*.json condensed_from[]. Print ARCHIVE:<bundle-path>
#                   on line 1, then extracted artifact content JSON. Exit 0.
#   3. Not found:   structured diagnostic to stderr. Exit 1.
#
# Environment:
#   RESOLVE_REF_DOCS_ROOT  override the docs/ root (default: "docs" relative to CWD).
#                          Used by tests to point to temp fixtures.
set -euo pipefail

BASENAME="${1:-}"
if [ -z "$BASENAME" ]; then
  echo "Usage: resolve-ref.sh <basename>" >&2
  exit 1
fi

DOCS_DIR="${RESOLVE_REF_DOCS_ROOT:-docs}"
ARCHIVE_DIR="$DOCS_DIR/archive"

# Step 1: Active file search
ACTIVE_HIT=$(find "$DOCS_DIR" -name "$BASENAME" -not -path "$ARCHIVE_DIR/*" 2>/dev/null | head -1)
if [ -n "$ACTIVE_HIT" ]; then
  echo "$ACTIVE_HIT"
  exit 0
fi

# Steps 2-3: Archive search — scan condensed_from, extract matching artifact content
if [ -d "$ARCHIVE_DIR" ]; then
  set +e
  ARCHIVE_RESULT=$(python3 - "$ARCHIVE_DIR" "$BASENAME" <<'PYEOF'
import json, sys, os, glob

archive_dir = sys.argv[1]
basename = sys.argv[2]

for bundle_path in sorted(glob.glob(os.path.join(archive_dir, "*.json"))):
    try:
        with open(bundle_path) as f:
            bundle = json.load(f)
    except (json.JSONDecodeError, IOError):
        continue
    if basename in bundle.get("condensed_from", []):
        for artifact in bundle.get("artifacts", []):
            if artifact.get("filename") == basename:
                print(f"ARCHIVE:{bundle_path}")
                print(json.dumps(artifact["content"], indent=2))
                sys.exit(0)
        # In condensed_from but artifact entry missing — still report the bundle
        print(f"ARCHIVE:{bundle_path}")
        sys.exit(0)

sys.exit(1)
PYEOF
  )
  PY_EXIT=$?
  set -e
  if [ "$PY_EXIT" -eq 0 ]; then
    echo "$ARCHIVE_RESULT"
    exit 0
  fi
fi

# Step 4: Hard fail — structured diagnostic
BUNDLE_COUNT=0
if [ -d "$ARCHIVE_DIR" ]; then
  BUNDLE_COUNT=$(find "$ARCHIVE_DIR" -maxdepth 1 -name "*.json" 2>/dev/null | wc -l | tr -d ' ')
fi
{
  echo "ERROR: resolve-ref.sh: basename not found: '$BASENAME'"
  echo "  Basename sought:         $BASENAME"
  echo "  Active dirs searched:    $DOCS_DIR (recursive, excluding archive/)"
  echo "  Archive bundles scanned: $BUNDLE_COUNT bundle(s) in $ARCHIVE_DIR"
  echo "  NOTE: Since nothing is ever deleted, a miss is a pipeline-integrity bug, not a soft miss."
} >&2
exit 1
