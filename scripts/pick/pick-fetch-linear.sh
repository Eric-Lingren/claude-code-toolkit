#!/usr/bin/env bash
# pick-fetch-linear.sh — run a bucket's Linear IssueFilter and emit normalized
# candidate JSON (same shape as pick-fetch-gh.sh; see its header). Strictly
# read-only: one viewer query plus one issues query, no writes of any kind.
#
# Usage: pick-fetch-linear.sh <Org/Repo> <bucket-name> [--all-states]
#
# Pickable guard (every bucket): only tickets in a not-yet-started state
# (Linear state type backlog or unstarted: Backlog, Ready to Assign, Todo)
# are emitted. In Progress, In Review, Done, Canceled, and Triage are dropped.
# Parent tickets (any issue with sub-issues) are dropped too, since the work
# lives in their children. --all-states disables both the guard and the
# bucket's own state filter; focus-options.sh uses it to list projects from
# all current sprint work, including tickets already in flight.
#
# Bucket config (repo-policy.json pick_buckets.<name>):
#   filter   Linear IssueFilter object. The string "@me" anywhere is replaced
#            with the user's Linear user id (resolved via the viewer query).
#   sort     rank-render sort steps
#   focus    true when the bucket shows the focus menu (consumed by SKILL.md)
#
# Extra Linear-only candidate key: "prs" (linked PR urls). Linear's
# gitBranchName is emitted as "branch". Resolved (completed/canceled)
# blockers are dropped.
#
# Key resolution matches fetch-linear-standup.sh: LINEAR_API_KEY env, else
# ~/.dotfiles/local/secrets.env. The key is never printed.
#
# Test hooks: PICK_POLICY (repo-policy.json path); LINEAR_ISSUES_FIXTURE (path
# to a GraphQL "data" JSON with an issues.nodes list; skips network and auth).
set -euo pipefail

repo="${1:?usage: pick-fetch-linear.sh <Org/Repo> <bucket> [--all-states]}"
bucket="${2:?usage: pick-fetch-linear.sh <Org/Repo> <bucket> [--all-states]}"
all_states=0
[ "${3:-}" = "--all-states" ] && all_states=1
POLICY="${PICK_POLICY:-$(cd "$(dirname "$0")" && pwd)/../../resources/repo-policy.json}"
LINEAR_GRAPHQL="${LINEAR_GRAPHQL:-https://api.linear.app/graphql}"

if [ -z "${LINEAR_ISSUES_FIXTURE:-}" ] && [ -z "${LINEAR_API_KEY:-}" ]; then
  secrets="$HOME/.dotfiles/local/secrets.env"
  # shellcheck source=/dev/null
  [ -f "$secrets" ] && source "$secrets"
  if [ -z "${LINEAR_API_KEY:-}" ]; then
    echo "LINEAR_API_KEY not set — add it to ~/.dotfiles/local/secrets.env" >&2
    exit 1
  fi
fi
export LINEAR_API_KEY="${LINEAR_API_KEY:-}"

ALL_STATES="$all_states" REPO="$repo" BUCKET="$bucket" POLICY="$POLICY" GQL="$LINEAR_GRAPHQL" python3 - <<'PY'
import json, os, sys, urllib.request

repo, bucket = os.environ["REPO"], os.environ["BUCKET"]
cfg = (json.load(open(os.environ["POLICY"])).get(repo) or {}).get("pick_buckets", {}).get(bucket)
if not cfg or "filter" not in cfg:
    sys.stderr.write(f"unknown bucket '{bucket}' for {repo}\n")
    sys.exit(2)

ISSUES_Q = """
query PickIssues($filter: IssueFilter) {
  issues(filter: $filter, first: 100) {
    nodes {
      identifier title url estimate createdAt branchName
      state { name type }
      project { name }
      parent { identifier }
      children(first: 1) { nodes { identifier } }
      assignee { name }
      labels { nodes { name } }
      attachments { nodes { url sourceType } }
      inverseRelations {
        nodes {
          type
          issue { identifier branchName state { name type } attachments { nodes { url sourceType } } }
        }
      }
    }
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


def subst(o, me):
    if o == "@me":
        return me
    if isinstance(o, dict):
        return {k: subst(v, me) for k, v in o.items()}
    if isinstance(o, list):
        return [subst(v, me) for v in o]
    return o


all_states = os.environ.get("ALL_STATES") == "1"
PICKABLE = ("backlog", "unstarted")  # Linear state types not yet started

fixture = os.environ.get("LINEAR_ISSUES_FIXTURE")
if fixture:
    data = json.load(open(fixture))
else:
    me = gql("query { viewer { id } }")["viewer"]["id"]
    flt = {k: v for k, v in cfg["filter"].items() if not (all_states and k == "state")}
    data = gql(ISSUES_Q, {"filter": subst(flt, me)})


def prs(att):
    return [a["url"] for a in (att or {}).get("nodes", []) if "/pull/" in (a.get("url") or "")]


out = []
for n in data["issues"]["nodes"]:
    if not all_states:
        if n["state"]["type"] not in PICKABLE:
            continue  # already in flight, done, canceled, or in triage
        if (n.get("children") or {}).get("nodes"):
            continue  # parent ticket: pick its sub-issues instead
    blockers = []
    for rel in (n.get("inverseRelations") or {}).get("nodes", []):
        if rel.get("type") != "blocks":
            continue
        b = rel["issue"]
        if b["state"]["type"] in ("completed", "canceled"):
            continue
        blockers.append({"id": b["identifier"], "state": b["state"]["name"],
                         "pr_in_review": bool(prs(b.get("attachments"))) and b["state"]["name"] == "In Review",
                         "branch": b.get("branchName")})
    assignee = (n.get("assignee") or {}).get("name")
    out.append({
        "id": n["identifier"], "source": "linear", "repo": repo,
        "title": n["title"], "url": n["url"],
        "labels": [l["name"] for l in (n.get("labels") or {}).get("nodes", [])],
        "assignees": [assignee] if assignee else [],
        "points": n.get("estimate"), "state": n["state"]["name"],
        "parent": (n.get("parent") or {}).get("identifier"),
        "project": (n.get("project") or {}).get("name"),
        "blockers": blockers, "prs": prs(n.get("attachments")),
        "branch": n["branchName"], "created_at": n.get("createdAt"),
    })
print(json.dumps({"repo": repo, "bucket": bucket, "sort": cfg.get("sort", []),
                  "focus": bool(cfg.get("focus")), "candidates": out}, indent=2))
PY
