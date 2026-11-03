#!/usr/bin/env python3
"""Poll GitHub for PRs that request the viewer's review by name.

A PR counts when it is open, not a draft, not bot-authored, and the viewer is
a directly requested reviewer (a team-only request does not count). PRs someone
else already approved still count. Each REVIEW_REQUESTED_EVENT for the viewer
that is still pending yields one request_seen event keyed by the timeline event
node id, so a re-request on an unchanged head commit is a new event and the
same event is never logged twice. New commits alone produce nothing.

The effective timestamp is the later of the request and the latest
READY_FOR_REVIEW_EVENT (a PR that went draft -> ready after the request). Both
are stored in the event data.

Usage: poll.py [--dry-run] [--json]
  --dry-run  print what would be logged, write nothing
  --json     print newly-seen requests as a JSON list
Read-only against GitHub (one `gh api graphql` query). REVIEW_WATCHER_GH
overrides the gh binary (tests); REVIEW_WATCHER_HOME relocates state.
"""
import argparse
import json
import os
import subprocess
import sys

import events

QUERY = """
query {
  viewer { login }
  search(query: "is:pr is:open review-requested:@me archived:false", type: ISSUE, first: 100) {
    nodes {
      ... on PullRequest {
        number
        isDraft
        headRefOid
        url
        title
        repository { nameWithOwner }
        author { __typename login }
        reviewRequests(first: 50) {
          nodes { requestedReviewer { __typename ... on User { login } } }
        }
        timelineItems(last: 100, itemTypes: [REVIEW_REQUESTED_EVENT, READY_FOR_REVIEW_EVENT]) {
          nodes {
            __typename
            ... on ReviewRequestedEvent {
              id
              createdAt
              requestedReviewer { __typename ... on User { login } }
            }
            ... on ReadyForReviewEvent { id createdAt }
          }
        }
      }
    }
  }
}
"""


def fetch():
    gh = os.environ.get("REVIEW_WATCHER_GH", "gh")
    proc = subprocess.run([gh, "api", "graphql", "-f", f"query={QUERY}"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)["data"]


def _is_user(reviewer, login):
    return bool(reviewer) and reviewer.get("__typename") == "User" and reviewer.get("login") == login


def _is_bot(author):
    if not author:
        return False
    return author.get("__typename") == "Bot" or (author.get("login") or "").endswith("[bot]")


def find_requests(data):
    """Return request dicts (one per pending direct request event)."""
    login = data["viewer"]["login"]
    out = []
    for pr in data["search"]["nodes"]:
        if not pr or "number" not in pr or pr.get("isDraft") or _is_bot(pr.get("author")):
            continue
        pending = [n for n in pr["reviewRequests"]["nodes"]
                   if _is_user(n.get("requestedReviewer"), login)]
        if not pending:
            continue
        items = pr["timelineItems"]["nodes"]
        mine = [i for i in items if i.get("__typename") == "ReviewRequestedEvent"
                and _is_user(i.get("requestedReviewer"), login)]
        if not mine:
            continue
        # Only the newest request is still pending; earlier ones were fulfilled.
        req = max(mine, key=lambda i: i["createdAt"])
        ready = [i["createdAt"] for i in items if i.get("__typename") == "ReadyForReviewEvent"]
        ready_at = max(ready) if ready else None
        ts = max(req["createdAt"], ready_at) if ready_at else req["createdAt"]
        out.append({
            "repo": pr["repository"]["nameWithOwner"], "pr": pr["number"],
            "request_id": req["id"], "head_sha": pr["headRefOid"], "ts": ts,
            "data": {"requested_at": req["createdAt"], "ready_at": ready_at,
                     "url": pr.get("url"), "title": pr.get("title"),
                     "author": (pr.get("author") or {}).get("login")},
        })
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    try:
        reqs = find_requests(fetch())
    except (RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"poll.py: {exc}", file=sys.stderr)
        return 1
    known = {e["request_id"] for e in events.read() if e["type"] == "request_seen"}
    new = []
    for r in reqs:
        if r["request_id"] in known:
            continue
        if not args.dry_run:
            _, created = events.append("request_seen", r["repo"], r["pr"], r["request_id"],
                                       r["head_sha"], r["ts"], r["data"])
            if not created:
                continue
        new.append(r)
    if args.json:
        print(json.dumps(new, indent=2))
    else:
        verb = "would log" if args.dry_run else "request-seen"
        for r in new:
            print(f"{verb} {r['repo']}#{r['pr']} {r['request_id']} {r['ts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
