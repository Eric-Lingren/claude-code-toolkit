#!/usr/bin/env bash
# ci-post-snapshot-comment.sh — create or update the single test-snapshot PR comment
# What: posts the Markdown file built by ci-snapshot-diff.py as a PR comment. If an earlier comment
#   carrying the snapshot marker exists on the PR it is edited in place, so repeated pushes do not pile up comments.
# When: from the informational Test snapshot workflow only, after ci-snapshot-diff.py wrote a comment file.
#   When the comment file is absent (nothing changed) and an earlier marker comment exists, that comment is
#   PATCHed to a short 'unchanged' body so a fixed regression does not stay flagged; otherwise skips quietly. Needs GH_TOKEN, and a token that
#   can write PR comments (fork PRs cannot, so the workflow step is continue-on-error).
#
# Usage: ci-post-snapshot-comment.sh <repo Org/Repo> <pr number> <comment file>
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: ci-post-snapshot-comment.sh <Org/Repo> <pr-number> <comment-file>" >&2
  exit 2
fi
repo="$1" pr="$2" file="$3"

marker='<!-- test-snapshot-diff -->'
# Only trust comments authored by the Actions bot, and take the first id from captured output
# (piping --paginate into head -n 1 would SIGPIPE gh and fail under pipefail).
matches="$(gh api "repos/$repo/issues/$pr/comments" --paginate \
  --jq ".[] | select(.user.login == \"github-actions[bot]\" and (.body | contains(\"$marker\"))) | .id")"
existing="${matches%%$'\n'*}"

if [[ ! -s "$file" ]]; then
  if [[ -z "$existing" ]]; then
    echo "no comment file at $file; passing set unchanged, nothing to post"
    exit 0
  fi
  # An earlier run left a (possibly "Newly failing") comment; replace it so it does not go stale.
  body="$marker
Test snapshot: the set of passing test files is unchanged from the base branch (an earlier report on this PR no longer applies)."
  gh api -X PATCH "repos/$repo/issues/comments/$existing" -f "body=$body" >/dev/null
  echo "no comment file at $file; marked stale comment $existing as unchanged"
  exit 0
fi

body="$(cat "$file")"

if [[ -n "$existing" ]]; then
  gh api -X PATCH "repos/$repo/issues/comments/$existing" -f "body=$body" >/dev/null
  echo "updated comment $existing"
else
  gh api -X POST "repos/$repo/issues/$pr/comments" -f "body=$body" >/dev/null
  echo "posted new comment"
fi
