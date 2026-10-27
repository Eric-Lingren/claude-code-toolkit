#!/usr/bin/env bash
# Resolve one GitHub PR review thread by GraphQL node id. Idempotent, single write, no retry.
# Prints one JSON line: {"thread_id":...,"status":"resolved|already_resolved|would_resolve|failed","error":...}
set -euo pipefail

usage() {
  echo "usage: resolve-review-thread.sh --thread-id <PRRT_...> [--dry-run]" >&2
  exit 2
}

THREAD_ID=""
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --thread-id) THREAD_ID="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) echo "error: unknown argument: $1" >&2; usage ;;
  esac
done

[ -n "$THREAD_ID" ] || usage
case "$THREAD_ID" in
  PRRT_*) ;;
  *) echo "error: --thread-id must be a review thread node id (PRRT_...), got: $THREAD_ID" >&2; exit 2 ;;
esac

emit() {
  python3 -c 'import json,sys; print(json.dumps({"thread_id":sys.argv[1],"status":sys.argv[2],"error":sys.argv[3] or None}))' \
    "$THREAD_ID" "$1" "${2:-}"
}

if ! state=$(gh api graphql -f id="$THREAD_ID" \
  -f query='query($id: ID!) { node(id: $id) { ... on PullRequestReviewThread { isResolved } } }' \
  --jq '.data.node.isResolved' 2>&1); then
  emit failed "lookup failed: $state"
  exit 1
fi

case "$state" in
  true) emit already_resolved; exit 0 ;;
  false) ;;
  *) emit failed "thread not found or not a review thread: $state"; exit 1 ;;
esac

if [ "$DRY_RUN" -eq 1 ]; then
  emit would_resolve
  exit 0
fi

# One write attempt only. The caller owns retries, with user confirmation.
if ! out=$(gh api graphql -f id="$THREAD_ID" \
  -f query='mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }' \
  --jq '.data.resolveReviewThread.thread.isResolved' 2>&1); then
  emit failed "mutation failed: $out"
  exit 1
fi

if [ "$out" = "true" ]; then
  emit resolved
else
  emit failed "unexpected mutation result: $out"
  exit 1
fi
