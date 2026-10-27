#!/usr/bin/env bash
# pick-fetch-gh.sh — run a bucket's native GitHub search and emit normalized
# candidate JSON. Strictly read-only (gh issue list only).
#
# Usage: pick-fetch-gh.sh <Org/Repo> <bucket-name>
#
# Output (stdout): {"repo","bucket","sort":[...],"candidates":[Candidate,...]}
# Candidate (shared shape; the Linear fetch emits the same keys):
#   id            "#123" (GitHub) or "QW-1482" (Linear)
#   source        "github" | "linear"
#   repo          Org/Repo the ticket's code lives in
#   title, url
#   labels        [string]
#   assignees     [string]
#   points        number|null   (null on GitHub)
#   state         string
#   parent        string|null   (epic/parent id)
#   project       string|null
#   blockers      [{"id","state","pr_in_review"}]  (GitHub: "blocked by #N" in body,
#                 state/pr_in_review null until blocker tiers resolve them)
#   branch        suggested branch name for the start line
#   created_at    ISO timestamp
#
# Test hooks: PICK_POLICY (repo-policy.json path), GH_ISSUE_LIST_FIXTURE (path
# to `gh issue list --json` output replacing the real gh call).
set -euo pipefail

repo="${1:?usage: pick-fetch-gh.sh <Org/Repo> <bucket>}"
bucket="${2:?usage: pick-fetch-gh.sh <Org/Repo> <bucket>}"
POLICY="${PICK_POLICY:-$(cd "$(dirname "$0")" && pwd)/../../resources/repo-policy.json}"

cfg=$(REPO="$repo" BUCKET="$bucket" POLICY="$POLICY" python3 - <<'PY'
import json, os, sys
b = (json.load(open(os.environ["POLICY"])).get(os.environ["REPO"]) or {}).get("pick_buckets", {}).get(os.environ["BUCKET"])
if not b:
    sys.stderr.write(f"unknown bucket '{os.environ['BUCKET']}' for {os.environ['REPO']}\n")
    sys.exit(2)
print(json.dumps(b))
PY
)
where=$(printf '%s' "$cfg" | python3 -c 'import json,sys;print(json.load(sys.stdin)["where"])')

if [ -n "${GH_ISSUE_LIST_FIXTURE:-}" ]; then
  raw=$(cat "$GH_ISSUE_LIST_FIXTURE")
else
  raw=$(gh issue list --repo "$repo" --search "$where" --limit 100 \
    --json number,title,body,labels,assignees,url,createdAt,state)
fi

RAW="$raw" REPO="$repo" BUCKET="$bucket" CFG="$cfg" python3 - <<'PY'
import json, os, re
cfg = json.loads(os.environ["CFG"])
repo = os.environ["REPO"]

def slug(title, n):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    s = "-".join(s.split("-")[:6])[:40].strip("-")
    return f"{s}-{n}" if s else f"issue-{n}"

out = []
for i in json.loads(os.environ["RAW"]):
    body = i.get("body") or ""
    blockers = [{"id": f"#{m}", "state": None, "pr_in_review": None}
                for m in dict.fromkeys(re.findall(r"(?i)blocked[ -]by:?\s*#(\d+)", body))]
    out.append({
        "id": f"#{i['number']}", "source": "github", "repo": repo,
        "title": i["title"], "url": i["url"],
        "labels": [l["name"] for l in i.get("labels", [])],
        "assignees": [a["login"] for a in i.get("assignees", [])],
        "points": None, "state": (i.get("state") or "open").lower(),
        "parent": None, "project": None, "blockers": blockers,
        "branch": slug(i["title"], i["number"]),
        "created_at": i.get("createdAt"),
    })
print(json.dumps({"repo": repo, "bucket": os.environ["BUCKET"],
                  "sort": cfg.get("sort", []), "candidates": out}, indent=2))
PY
