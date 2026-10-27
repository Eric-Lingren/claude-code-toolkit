#!/usr/bin/env bash
# pick-fetch-linear-activity.sh — emit teammates' in-progress Linear tickets as
# JSON [{id,parent,project,assignee}] for overlap.py. Strictly read-only: one
# viewer query plus one issues query.
#
# Usage: pick-fetch-linear-activity.sh
# Key resolution as pick-fetch-linear.sh. Test hook: LINEAR_ACTIVITY_FIXTURE
# (path to a GraphQL "data" JSON with issues.nodes; skips network and auth).
set -euo pipefail

LINEAR_GRAPHQL="${LINEAR_GRAPHQL:-https://api.linear.app/graphql}"
if [ -z "${LINEAR_ACTIVITY_FIXTURE:-}" ] && [ -z "${LINEAR_API_KEY:-}" ]; then
  secrets="$HOME/.dotfiles/local/secrets.env"
  # shellcheck source=/dev/null
  [ -f "$secrets" ] && source "$secrets"
  if [ -z "${LINEAR_API_KEY:-}" ]; then
    echo "LINEAR_API_KEY not set — add it to ~/.dotfiles/local/secrets.env" >&2
    exit 1
  fi
fi
export LINEAR_API_KEY="${LINEAR_API_KEY:-}"

GQL="$LINEAR_GRAPHQL" python3 - <<'PY'
import json, os, sys, urllib.request

Q = """
query PickActivity($filter: IssueFilter) {
  issues(filter: $filter, first: 100) {
    nodes { identifier project { name } parent { identifier } assignee { name } }
  }
}
"""


def gql(query, variables=None):
    req = urllib.request.Request(
        os.environ["GQL"],
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Content-Type": "application/json", "Authorization": os.environ["LINEAR_API_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    if d.get("errors"):
        sys.stderr.write("; ".join(e.get("message", "error") for e in d["errors"]) + "\n")
        sys.exit(1)
    return d["data"]


fx = os.environ.get("LINEAR_ACTIVITY_FIXTURE")
if fx:
    data = json.load(open(fx))
else:
    me = gql("query { viewer { id } }")["viewer"]["id"]
    data = gql(Q, {"filter": {"state": {"type": {"eq": "started"}},
                              "assignee": {"id": {"neq": me}, "null": False}}})
print(json.dumps([
    {"id": n["identifier"], "parent": (n.get("parent") or {}).get("identifier"),
     "project": (n.get("project") or {}).get("name"),
     "assignee": (n.get("assignee") or {}).get("name")}
    for n in data["issues"]["nodes"]], indent=2))
PY
