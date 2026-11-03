#!/usr/bin/env bash
# Find open issues in ONE repo that may duplicate a capture.
#   find-dupes.sh <org/repo> <key terms>      -> JSON [{number,title,url}]
#   find-dupes.sh --extract-terms <title>     -> key terms from a title
set -euo pipefail

extract_terms() {
  # lowercase, strip punctuation, drop stopwords and short words, dedupe, keep first 6
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9\n' ' ' | tr -s ' ' '\n' |
    awk 'BEGIN{n=split("the a an and or of to in on for with is are was be it this that at by from as not but",s," ");for(i=1;i<=n;i++)stop[s[i]]=1}
         length($0)>2 && !($0 in stop) && !seen[$0]++ {out=out (out?" ":"") $0; if(++c==6) exit}
         END{print out}'
}

if [ "${1:-}" = "--extract-terms" ]; then
  [ $# -ge 2 ] || { echo "usage: find-dupes.sh --extract-terms <title>" >&2; exit 1; }
  extract_terms "$2"
  exit 0
fi

if [ $# -lt 2 ] || [ -z "$1" ] || [ -z "$2" ]; then
  echo "usage: find-dupes.sh <org/repo> <key terms>" >&2
  exit 1
fi

REPO="$1"
TERMS="$2"
case "$REPO" in
  */*/*|/*|*/|*[!A-Za-z0-9._/-]*) echo "error: repo must be org/repo, got '$REPO'" >&2; exit 1 ;;
  */*) ;;
  *) echo "error: repo must be org/repo, got '$REPO'" >&2; exit 1 ;;
esac

gh issue list --repo "$REPO" --state open --search "$TERMS" --json number,title,url |
  jq -c '[.[] | {number, title, url}]'
