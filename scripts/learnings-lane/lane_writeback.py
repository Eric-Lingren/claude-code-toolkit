#!/usr/bin/env python3
"""lane_writeback.py: copy lane PR outcomes back onto the learnings they carried.

What: poll lane PRs (label LANE_LABEL). Merged PR: member learnings -> status applied + merged_sha.
  Closed without merge: member learnings -> status rejected + the user's close comment as reason,
  so the selector never re-proposes them. Open PR: nothing. Member ids come from the
  "Learning-Ids:" commit trailers on the PR commits (comma or space separated), so no new state is
  stored. Learnings already applied or rejected are skipped, so a second run changes nothing.
  Headless runs never set applied themselves; only this write-back does.
  The changes-requested path stays in Phase 3.
  escape_point: on merge, per commit, derive_sections() (write-change-records.py) over the commit's
  changed files. Exactly one markdown section touched in total -> stamped on every learning that
  commit's trailer names. Several sections, a non-markdown file (e.g. JSON), or an unreadable
  commit -> left unset. Existing escape_point is never overwritten. The clustering model never
  writes it.
When: start of every kick, on the main checkout. Updates go through update-learning.py, then
  learnings_push.py pushes the file (only when something changed).
Trust: gh_pr_state keeps only PRs whose head branch starts with LANE_BRANCH_PREFIX, and takes the
  close comment only from comments whose authorAssociation is OWNER. Reason is passed as
  --reason=<text> so a dash-led reason is not parsed as a flag.
Test hook: pr_state is an injectable callable returning a list of
  {"number", "state": OPEN|MERGED|CLOSED, "merge_sha", "close_comment", "commit_messages": [str],
   "commits": [{"sha", "message"}] (optional; enables escape_point)}.
  Tests never call GitHub; set LOG_LEARNING_DEST to a temp learnings dir.
Repo for git diffs: writeback(repo=...); the CLI passes --repo or the cwd's git toplevel.
CLI: lane_writeback.py [--repo PATH] [--no-push]
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPDATE_LEARNING = HERE.parent / "learning" / "update-learning.py"


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, HERE / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_wcr_spec = importlib.util.spec_from_file_location(
    "write_change_records", HERE.parent / "attribution" / "write-change-records.py")
write_change_records = importlib.util.module_from_spec(_wcr_spec)
_wcr_spec.loader.exec_module(write_change_records)

config = _load("lane_config", "config.py")
lane_select = _load("lane_select_wb", "lane_select.py")

TRAILER_RE = re.compile(r"^Learning-Ids:[ \t]*(.+)$", re.IGNORECASE | re.MULTILINE)
DONE_STATUSES = ("applied", "rejected")
OWNER_ASSOCIATION = "OWNER"
NO_REASON = "closed without merge (no comment)"


def trailer_ids(messages):
    """Ordered, de-duplicated learning ids from Learning-Ids trailers across commit messages."""
    seen = []
    for msg in messages:
        for m in TRAILER_RE.finditer(msg or ""):
            for tok in re.split(r"[,\s]+", m.group(1).strip()):
                if tok and tok not in seen:
                    seen.append(tok)
    return seen


def gh_pr_state(cwd=None):
    """All lane PRs (any state) via gh. Default for pr_state."""
    out = subprocess.run(
        ["gh", "pr", "list", "--state", "all", "--label", config.LANE_LABEL, "--limit", str(config.GH_LIST_LIMIT_ALL_PRS),
         "--json", "number,state,mergeCommit,comments,commits,headRefName"],
        capture_output=True, text=True, timeout=60, check=True, cwd=cwd).stdout
    prs = []
    for p in json.loads(out):
        if not (p.get("headRefName") or "").startswith(config.LANE_BRANCH_PREFIX):
            continue
        comments = [c for c in (p.get("comments") or [])
                    if (c.get("authorAssociation") or "").upper() == OWNER_ASSOCIATION]
        prs.append({
            "number": p["number"],
            "state": p["state"],
            "merge_sha": (p.get("mergeCommit") or {}).get("oid"),
            "close_comment": (comments[-1].get("body") if comments else "") or "",
            "commits": [{"sha": c.get("oid"),
                         "message": (c.get("messageHeadline", "") + "\n\n" + c.get("messageBody", ""))}
                        for c in p.get("commits") or []],
            "commit_messages": [(c.get("messageHeadline", "") + "\n\n" + c.get("messageBody", ""))
                                for c in p.get("commits") or []],
        })
    return prs


def _rows_by_id(learnings_dir):
    path = Path(learnings_dir) / "unified-learnings.jsonl"
    return {row["id"]: row for row in lane_select.load_rows(path) if row.get("id")}


def commit_escape_point(repo, sha):
    """{"path", "section"} when the commit touches exactly one markdown section, else None."""
    git = write_change_records.git
    try:
        names = git(repo, "diff", "--name-only", "--no-renames", "-z", f"{sha}^", sha,
                    check=False).stdout.split("\0")
        paths = [n for n in names if n]
        if not paths:
            return None
        found = []
        for path in paths:
            if not path.endswith(".md"):
                return None
            sections = write_change_records.derive_sections(repo, f"{sha}^", sha, path)
            if write_change_records.PREAMBLE in sections:
                return None
            found.extend((path, sec) for sec in sections)
    except Exception:
        return None
    if len(found) != 1:
        return None
    return {"path": found[0][0], "section": found[0][1]}


def _update(args, env):
    return subprocess.run([sys.executable, str(UPDATE_LEARNING), *args], capture_output=True,
                          text=True, env=env, check=False)


def writeback(pr_state=gh_pr_state, learnings_dir=None, repo=None):
    """Apply PR outcomes. Returns {"applied": [ids], "rejected": [ids], "errors": [str]}."""
    if learnings_dir is None:
        learnings_dir = os.environ.get("LOG_LEARNING_DEST") or str(lane_select.UNIFIED.parent)
    env = dict(os.environ, LOG_LEARNING_DEST=str(learnings_dir))
    rows = _rows_by_id(learnings_dir)
    statuses = {i: r.get("status") for i, r in rows.items()}
    result = {"applied": [], "rejected": [], "errors": []}
    for pr in pr_state():
        state = (pr.get("state") or "").upper()
        if state not in ("MERGED", "CLOSED"):
            continue
        if state == "MERGED" and not pr.get("merge_sha"):
            result["errors"].append(f"PR {pr.get('number')}: merged but no merge sha")
            continue
        escape = {}
        if state == "MERGED" and repo:
            pending = {lid for lid in trailer_ids(pr.get("commit_messages") or [])
                       if lid in statuses and statuses[lid] not in DONE_STATUSES
                       and not rows[lid].get("escape_point")}
            for c in (pr.get("commits") or []) if pending else []:
                need = [lid for lid in trailer_ids([c.get("message")])
                        if lid in pending and lid not in escape]
                if not need:
                    continue
                ep = commit_escape_point(repo, c.get("sha"))
                if ep:
                    for lid in need:
                        escape[lid] = ep
        for lid in trailer_ids(pr.get("commit_messages") or []):
            if lid not in statuses or statuses[lid] in DONE_STATUSES:
                continue
            if state == "MERGED":
                args = ["--id", lid, "--status", "applied", "--merged-sha", pr["merge_sha"]]
                key = "applied"
                if lid in escape and not rows[lid].get("escape_point"):
                    args += ["--escape-point", json.dumps(escape[lid])]
            else:
                reason = (pr.get("close_comment") or "").strip() or NO_REASON
                args = ["--id", lid, "--status", "rejected", f"--reason={reason}"]
                key = "rejected"
            proc = _update(args, env)
            if proc.returncode != 0:
                result["errors"].append(f"{lid}: {proc.stderr.strip()}")
                continue
            statuses[lid] = key
            result[key].append(lid)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=None, help="main checkout (default: git toplevel of cwd)")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args(argv)
    repo = args.repo or subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                       capture_output=True, text=True).stdout.strip()
    res = writeback(repo=repo)
    print(json.dumps(res))
    for e in res["errors"]:
        print(f"warning: {e}", file=sys.stderr)
    if (res["applied"] or res["rejected"]) and not args.no_push:
        push = _load("learnings_push", "learnings_push.py")
        print(push.push_learnings(repo)["message"])
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
