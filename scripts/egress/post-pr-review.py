#!/usr/bin/env python3
"""Post review findings to a GitHub PR as one review with per-line inline comments.

Input: JSON array of {"file", "line", "body"} objects. "body" is the full comment text.

GitHub rejects the entire review if any inline comment targets a line outside the
diff, so each finding is checked against the PR's new-side diff lines first. Findings
with line 0, or on a line outside the diff, are folded into the review summary body.

With --append-pending, inline comments are added to the caller's existing pending
review (left unsubmitted) instead of creating a new one. Folded findings are refused in
that mode, since writing them would overwrite the draft's summary body.

Usage:
  post-pr-review.py --pr 123 --findings findings.json [--summary "text"] [--repo owner/name] [--append-pending] [--dry-run]

Prints the review URL on success. Exits non-zero on gh/API failure.
"""

import argparse
import json
import re
import subprocess
import sys

HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def run_gh(args, stdin=None):
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"gh {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def commentable_lines(diff_text):
    """Map new-file path -> set of new-side line numbers GitHub accepts comments on."""
    lines_by_file = {}
    current_file = None
    counter = None
    for row in diff_text.splitlines():
        if row.startswith("+++ "):
            path = row[4:]
            current_file = path[2:] if path.startswith("b/") else None
            counter = None
            continue
        if row.startswith("--- ") or row.startswith("diff --git"):
            continue
        header = HUNK_HEADER.match(row)
        if header:
            counter = int(header.group(1))
            continue
        if current_file is None or counter is None:
            continue
        if row.startswith("+") or row.startswith(" "):
            lines_by_file.setdefault(current_file, set()).add(counter)
            counter += 1
        # "-" rows and "\ No newline" markers do not advance the new-side counter.
    return lines_by_file


PENDING_REVIEW_QUERY = """
query($owner: String!, $name: String!, $pr: Int!) {
  viewer { login }
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      reviews(states: PENDING, first: 10) { nodes { id author { login } } }
    }
  }
}
"""

ADD_THREAD_MUTATION = """
mutation($input: AddPullRequestReviewThreadInput!) {
  addPullRequestReviewThread(input: $input) { thread { id } }
}
"""


def graphql(query, variables):
    response = json.loads(run_gh(
        ["api", "graphql", "--input", "-"],
        stdin=json.dumps({"query": query, "variables": variables}),
    ))
    if response.get("errors"):
        sys.exit(f"GraphQL error: {json.dumps(response['errors'])}")
    return response["data"]


def find_pending_review_id(repo, pr_number):
    owner, name = repo.split("/", 1)
    data = graphql(PENDING_REVIEW_QUERY, {"owner": owner, "name": name, "pr": int(pr_number)})
    viewer = data["viewer"]["login"]
    reviews = data["repository"]["pullRequest"]["reviews"]["nodes"]
    mine = [review["id"] for review in reviews if (review.get("author") or {}).get("login") == viewer]
    if not mine:
        sys.exit(f"No pending review by {viewer} on PR {pr_number}.")
    return mine[0]


def append_to_pending(repo, pr_number, inline_comments):
    review_id = find_pending_review_id(repo, pr_number)
    for index, comment in enumerate(inline_comments):
        thread_input = {
            "pullRequestReviewId": review_id,
            "path": comment["path"],
            "line": comment["line"],
            "side": comment["side"],
            "body": comment["body"],
        }
        try:
            graphql(ADD_THREAD_MUTATION, {"input": thread_input})
        except SystemExit as error:
            sys.exit(f"Added {index} of {len(inline_comments)} before failing on "
                     f"{comment['path']}:L{comment['line']}. Not retrying.\n{error}")
    return review_id


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pr", required=True, help="PR number")
    parser.add_argument("--findings", required=True, help="Path to findings JSON array")
    parser.add_argument("--summary", default="", help="Review summary body (verdict, counts)")
    parser.add_argument("--repo", help="owner/name; defaults to the current repo")
    parser.add_argument("--append-pending", action="store_true",
                        help="Add inline comments to your existing pending review; leave it unsubmitted")
    parser.add_argument("--dry-run", action="store_true", help="Print the payload, do not post")
    args = parser.parse_args()

    with open(args.findings) as handle:
        findings = json.load(handle)

    repo_flag = ["--repo", args.repo] if args.repo else []
    pr_meta = json.loads(run_gh(["pr", "view", args.pr, *repo_flag, "--json", "headRefOid,url"]))
    repo = args.repo or run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]).strip()
    valid_lines = commentable_lines(run_gh(["pr", "diff", args.pr, *repo_flag]))

    inline_comments = []
    folded = []
    for finding in findings:
        path, line, body = finding["file"], int(finding.get("line") or 0), finding["body"]
        if line > 0 and line in valid_lines.get(path, set()):
            inline_comments.append({"path": path, "line": line, "side": "RIGHT", "body": body})
        else:
            location = f"`{path}`" if line == 0 else f"`{path}:L{line}` (outside diff)"
            folded.append(f"- {location}: {body}")

    summary_parts = [args.summary.strip()] if args.summary.strip() else []
    if folded:
        summary_parts.append("**File-level / outside-diff findings**\n\n" + "\n".join(folded))

    payload = {
        "commit_id": pr_meta["headRefOid"],
        "event": "COMMENT",
        "body": "\n\n".join(summary_parts),
        "comments": inline_comments,
    }

    if args.append_pending and folded:
        sys.exit("--append-pending can't fold findings into the draft body. Fix their lines or drop them.")

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        print(f"inline: {len(inline_comments)}  folded: {len(folded)}", file=sys.stderr)
        return

    if args.append_pending:
        append_to_pending(repo, args.pr, inline_comments)
        print(f"inline: {len(inline_comments)}  folded: 0  (added to pending review, not submitted)")
        print(pr_meta["url"])
        return

    response = json.loads(run_gh(
        ["api", "--method", "POST", f"/repos/{repo}/pulls/{args.pr}/reviews", "--input", "-"],
        stdin=json.dumps(payload),
    ))
    print(f"inline: {len(inline_comments)}  folded: {len(folded)}")
    print(response.get("html_url", pr_meta["url"]))


if __name__ == "__main__":
    main()
