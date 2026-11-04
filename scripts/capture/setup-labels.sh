#!/usr/bin/env bash
# setup-labels.sh - curate Eric-Lingren/dotfiles labels to exactly: idea, fix, triage.
#
# Usage: setup-labels.sh [--apply] [--repo Eric-Lingren/dotfiles]
#   (default)  dry-run: prints planned actions, runs no mutating gh command
#   --apply    performs them
#
# Ensures idea, fix and the learnings-lane labels (learnings-lane, blocked, needs-grill, system-generated) exist; deletes the nine GitHub default labels only after
# confirming no issue (open or closed) uses them. Aborts listing offenders otherwise.
# Refuses any repo other than Eric-Lingren/dotfiles.
set -euo pipefail

ALLOWED_REPO="Eric-Lingren/dotfiles"
REPO="$ALLOWED_REPO"
APPLY=0
ENSURE=(idea fix learnings-lane blocked needs-grill system-generated)
DEFAULTS=("bug" "documentation" "duplicate" "enhancement" "good first issue" "help wanted" "invalid" "question" "wontfix")

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --repo) REPO="${2:-}"; shift ;;
    --repo=*) REPO="${1#--repo=}" ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$REPO" != "$ALLOWED_REPO" ]; then
  echo "refusing: this script only operates on $ALLOWED_REPO (got '$REPO')" >&2
  exit 2
fi

existing="$(gh label list --repo "$REPO" --limit 200 --json name --jq '.[].name')"
has_label() { printf '%s\n' "$existing" | grep -Fxq -- "$1"; }

to_create=()
for l in "${ENSURE[@]}"; do has_label "$l" || to_create+=("$l"); done

to_delete=()
for l in "${DEFAULTS[@]}"; do has_label "$l" && to_delete+=("$l"); done

offenders=()
for l in ${to_delete[@]+"${to_delete[@]}"}; do
  nums="$(gh issue list --repo "$REPO" --label "$l" --state all --limit 1000 --json number --jq '.[].number')"
  if [ -n "$nums" ]; then
    offenders+=("$l: #$(printf '%s' "$nums" | paste -sd' ' - | sed 's/ / #/g')")
  fi
done

mode="DRY-RUN"; [ "$APPLY" -eq 1 ] && mode="APPLY"
echo "[$mode] repo: $REPO"

if [ ${#offenders[@]} -gt 0 ]; then
  echo "default labels still used by issues:" >&2
  for o in "${offenders[@]}"; do echo "  $o" >&2; done
  if [ "$APPLY" -eq 1 ]; then
    echo "aborting: nothing was changed" >&2
    exit 1
  fi
  echo "(--apply would abort until these are resolved)" >&2
fi

for l in ${to_create[@]+"${to_create[@]}"}; do
  echo "create label: $l"
  if [ "$APPLY" -eq 1 ]; then gh label create "$l" --repo "$REPO"; fi
done
for l in ${to_delete[@]+"${to_delete[@]}"}; do
  echo "delete label: $l"
  if [ "$APPLY" -eq 1 ]; then gh label delete "$l" --repo "$REPO" --yes; fi
done
if [ ${#to_create[@]} -eq 0 ] && [ ${#to_delete[@]} -eq 0 ]; then echo "nothing to do"; fi
exit 0
