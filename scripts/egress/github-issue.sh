#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "usage: github-issue.sh <title> <body> <org/repo>" >&2
  exit 1
fi

TITLE="$1"
BODY="$2"
REPO="$3"

if [ -z "$TITLE" ] || [ -z "$BODY" ] || [ -z "$REPO" ]; then
  echo "error: title, body, and org/repo must all be non-empty" >&2
  exit 1
fi

gh issue create \
  --title "$TITLE" \
  --body "$BODY" \
  --label triage \
  --repo "$REPO"
