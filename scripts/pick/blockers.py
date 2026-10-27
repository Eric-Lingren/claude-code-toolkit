#!/usr/bin/env python3
"""Blocker tiers for /pick (read-only).

Tier per candidate (blockers = open blockers as emitted by pick-fetch-*):
  0 ready      no blockers, or every blocker merged/Done
  1 stackable  every open blocker has a PR in review and a known branch
  2 blocked    anything else (not started, in progress with no PR, branch unknown)
A stackable candidate gets `_base` = its blocker's branch; the start line is
'wt <branch> <base>' (base is the second positional arg, never a flag). With
several stackable blockers, the first one's branch is used.

Library: classify(c), apply(cands) -> (visible, hidden_count).
CLI:  blockers.py resolve [file]   fill GitHub blocker state/branch/pr_in_review
      from native issue dependencies + 'blocked by #N' body fallback.
Test hooks: PICK_GH_DEPS (JSON {"#N":[{"number","state"}]}), PICK_GH_ISSUES
(JSON {"#N":"open|closed"}), PICK_GH_PRS (gh pr list JSON with
number,headRefName,body,state,closingIssuesReferences).
"""
import json, os, re, subprocess, sys

DONE = {"done", "completed", "closed", "merged", "canceled", "cancelled"}


def open_blockers(c):
    return [b for b in c.get("blockers", []) if (b.get("state") or "").lower() not in DONE]


def classify(c):
    open_b = open_blockers(c)
    if not open_b:
        return 0, None
    if all(b.get("pr_in_review") and b.get("branch") for b in open_b):
        return 1, open_b[0]["branch"]
    return 2, None


def apply(cands):
    vis, hidden = [], 0
    for c in cands:
        t, base = classify(c)
        if t == 2:
            hidden += 1
            continue
        c["_tier"], c["_base"] = t, base
        vis.append(c)
    return vis, hidden


def _gh_json(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def _env_json(key):
    return json.loads(os.environ[key]) if os.environ.get(key) else None


def resolve(doc):
    """Fill GitHub blocker state/pr_in_review/branch in place."""
    repo = doc["repo"]
    prs = _env_json("PICK_GH_PRS")
    if prs is None:
        prs = _gh_json(["pr", "list", "--repo", repo, "--state", "all", "--limit", "100", "--json",
                        "number,headRefName,body,state,closingIssuesReferences"]) or []
    issues = _env_json("PICK_GH_ISSUES") or {}
    deps = _env_json("PICK_GH_DEPS")

    def pr_for(n):
        for p in prs:
            refs = [r.get("number") for r in p.get("closingIssuesReferences") or []]
            if int(n) in refs or re.search(rf"(?i)(closes|fixes|resolves)\s+#{n}\b", p.get("body") or ""):
                return p
        return None

    for c in doc["candidates"]:
        if c.get("source") != "github":
            continue
        have = {b["id"]: b for b in c.get("blockers", [])}  # body-text fallback
        if deps is not None:
            nat = deps.get(c["id"], [])
        else:
            nat = _gh_json(["api", f"repos/{repo}/issues/{c['id'][1:]}/dependencies/blocked_by"]) or []
        for d in nat:
            b = have.setdefault(f"#{d['number']}", {"id": f"#{d['number']}", "state": None})
            b["state"] = d.get("state") or b.get("state")
        for bid, b in have.items():
            n = bid[1:]
            st = b.get("state") or issues.get(bid)
            if st is None:
                st = (_gh_json(["issue", "view", n, "--repo", repo, "--json", "state"]) or {}).get("state")
            p = pr_for(n)
            pst = (p.get("state") or "").upper() if p else ""
            if pst == "MERGED":
                st = "merged"
            b["state"] = (st or "open").lower()
            b["pr_in_review"] = pst == "OPEN"
            b["branch"] = p["headRefName"] if pst == "OPEN" else None
        c["blockers"] = list(have.values())
    return doc


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "resolve":
        d = json.loads(open(sys.argv[2]).read() if len(sys.argv) > 2 else sys.stdin.read())
        print(json.dumps(resolve(d), indent=2))
    else:
        sys.exit("usage: blockers.py resolve [file]")
