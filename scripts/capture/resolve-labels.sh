#!/usr/bin/env bash
# resolve-labels.sh <org/repo> <idea|fix|bug>
# Prints the comma-separated issue labels for a capture kind, read from the
# repo's capture_labels map in repo-policy.json. "bug" is an alias for "fix".
# Exit 0 ok; 1 repo has no capture_labels (not a capture target) or kind unmapped; 2 usage.
# REPO_POLICY env overrides the policy file path (tests).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLICY="${REPO_POLICY:-$HERE/../../resources/repo-policy.json}"

if [ $# -ne 2 ]; then echo "usage: resolve-labels.sh <org/repo> <idea|fix|bug>" >&2; exit 2; fi
repo="$1"; kind="$2"
[ "$kind" = "bug" ] && kind="fix"
case "$kind" in idea|fix) ;; *) echo "unknown capture kind: $2 (expected idea, fix or bug)" >&2; exit 2 ;; esac

if ! jq -e --arg r "$repo" '.[$r].capture_labels | type == "object"' "$POLICY" >/dev/null 2>&1; then
  echo "$repo is not a capture target (no capture_labels entry in repo-policy.json)" >&2
  exit 1
fi
out="$(jq -r --arg r "$repo" --arg k "$kind" '.[$r].capture_labels[$k] // empty | join(",")' "$POLICY")"
if [ -z "$out" ]; then
  echo "$repo has no capture_labels mapping for '$kind'" >&2
  exit 1
fi
printf '%s\n' "$out"
