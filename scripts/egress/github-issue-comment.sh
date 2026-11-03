#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "usage: github-issue-comment.sh <issue-number> <body> <org/repo>" >&2
  exit 1
fi

ISSUE="$1"
BODY="$2"
REPO="$3"

if [ -z "$ISSUE" ] || [ -z "$BODY" ] || [ -z "$REPO" ]; then
  echo "error: issue number, body, and org/repo must all be non-empty" >&2
  exit 1
fi

case "$ISSUE" in
  *[!0-9]*)
    echo "error: issue number must be numeric, got '$ISSUE'" >&2
    exit 1
    ;;
esac

gh issue comment "$ISSUE" \
  --body "$BODY" \
  --repo "$REPO"
