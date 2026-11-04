#!/usr/bin/env python3
"""cross_cluster.py: promote cross-target root causes to idea issues.

What: promote(clusters, rows, create_issue, learnings_dir) groups clusters from all targets by
  normalized root_cause (case, whitespace and punctuation folded). A group spanning at least
  CROSS_TARGET_MIN distinct targets becomes one idea issue payload (title, body with evidence,
  member learning ids, targets, labels). create_issue(payload) -> issue number (int). Then every
  member learning is marked clustered:<issue#> through update-learning.py. Learnings already
  clustered are skipped; a group with no markable member creates no issue. Smaller groups: nothing.
  clustered:<issue#> rows are excluded from per-skill runs by lane_select.is_eligible_row.
When: kick script, on the main checkout only (never in a lane worktree).
Test hook: create_issue is injectable; tests never call GitHub. Set LOG_LEARNING_DEST for a temp dir.
CLI: cross_cluster.py --clusters CLUSTERS.json [--file UNIFIED] [--repo OWNER/REPO]
  CLUSTERS.json is a list of cluster objects (id, target, learning_ids, root_cause, fix).
"""
import argparse
import hashlib
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


config = _load("lane_config", "config.py")
lane_select = _load("lane_select_cc", "lane_select.py")

ISSUE_LABELS = ["idea", config.LANE_LABEL]


def root_key(text):
    return re.sub(r"[\W_]+", " ", (text or "").casefold()).strip()


def group_clusters(clusters):
    """Clusters grouped by normalized root cause, in first-seen order."""
    groups = {}
    for c in clusters:
        groups.setdefault(root_key(c.get("root_cause")), []).append(c)
    return groups


GROUP_KEY_PREFIX = "Group-Key:"


def group_key(group):
    """Stable id of a root-cause group; embedded in the issue body so re-runs can find the issue."""
    return hashlib.sha256(root_key(group[0].get("root_cause")).encode()).hexdigest()[:16]


def build_payload(group, rows_by_id):
    targets = sorted({c["target"] for c in group})
    ids = []
    for c in group:
        for lid in c["learning_ids"]:
            if lid not in ids:
                ids.append(lid)
    root = group[0]["root_cause"]
    lines = [f"Root cause seen across {len(targets)} targets: {', '.join(targets)}.", "",
             f"Root cause: {root}", "", "## Evidence"]
    for c in group:
        lines.append(f"- {c['target']} (cluster {c['id']}): fix: {c.get('fix', '')}")
        for lid in c["learning_ids"]:
            lines.append(f"  - {lid}: {(rows_by_id.get(lid) or {}).get('problem', '')}")
    gk = group_key(group)
    lines += ["", "Learning-Ids: " + ", ".join(ids), f"{GROUP_KEY_PREFIX} {gk}"]
    return {"title": f"Cross-target learning: {root}"[:120], "body": "\n".join(lines),
            "labels": list(ISSUE_LABELS), "targets": targets, "learning_ids": ids, "group_key": gk}


def gh_create_issue(payload, repo=None, cwd=None):
    """Create the idea issue via gh (export-tasks-github pattern); returns the issue number."""
    cmd = ["gh", "issue", "create", "--title", payload["title"], "--body", payload["body"],
           "--label", ",".join(payload["labels"])]
    if repo:
        cmd += ["--repo", repo]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True, cwd=cwd).stdout.strip()
    m = re.search(r"/issues/(\d+)\s*$", out)
    if not m:
        raise RuntimeError(f"cannot parse issue url: {out!r}")
    return int(m.group(1))


def gh_find_issue(payload, repo=None, cwd=None):
    """Existing issue (any state) whose body carries the payload's group key; its number, else None."""
    token = f"{GROUP_KEY_PREFIX} {payload['group_key']}"
    cmd = ["gh", "issue", "list", "--state", "all", "--label", payload["labels"][0],
           "--search", f'"{payload["group_key"]}" in:body', "--json", "number,body", "--limit", str(config.GH_LIST_LIMIT_ISSUES)]
    if repo:
        cmd += ["--repo", repo]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True, cwd=cwd).stdout
    for item in json.loads(out or "[]"):
        if token in (item.get("body") or ""):
            return int(item["number"])
    return None


def promote(clusters, rows, create_issue=gh_create_issue, learnings_dir=None, find_issue=None):
    """find_issue(payload) -> existing issue number or None; when given, an existing issue is reused
    (idempotent re-run after a partial mark failure) and a lookup failure skips the group.
    Returns {"issues": [{"number", "learning_ids", "targets"}], "marked": [ids], "errors": [str]}."""
    if learnings_dir is None:
        learnings_dir = os.environ.get("LOG_LEARNING_DEST") or str(lane_select.UNIFIED.parent)
    env = dict(os.environ, LOG_LEARNING_DEST=str(learnings_dir))
    by_id = {r["id"]: r for r in rows if r.get("id")}
    result = {"issues": [], "marked": [], "errors": []}
    for group in group_clusters(clusters).values():
        if len({c["target"] for c in group}) < config.CROSS_TARGET_MIN:
            continue
        payload = build_payload(group, by_id)
        # Skip members already clustered or unknown; no markable member, no issue.
        payload["learning_ids"] = [i for i in payload["learning_ids"]
                                   if i in by_id and lane_select.is_eligible_row(by_id[i])]
        if not payload["learning_ids"]:
            continue
        number = None
        if find_issue is not None:
            try:
                number = find_issue(payload)
            except Exception as e:  # noqa: BLE001 - never risk a duplicate issue
                result["errors"].append(f"issue lookup failed: {e}")
                continue
        if number is None:
            try:
                number = create_issue(payload)
            except Exception as e:  # noqa: BLE001 - report, keep going
                result["errors"].append(f"issue create failed: {e}")
                continue
        result["issues"].append({"number": number, "learning_ids": payload["learning_ids"],
                                 "targets": payload["targets"]})
        for lid in payload["learning_ids"]:
            p = subprocess.run([sys.executable, str(UPDATE_LEARNING), "--id", lid,
                                "--status", f"clustered:{number}"],
                               capture_output=True, text=True, env=env, check=False)
            if p.returncode != 0:
                result["errors"].append(f"{lid}: {p.stderr.strip()}")
            else:
                result["marked"].append(lid)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--clusters", required=True)
    ap.add_argument("--file", default=str(lane_select.UNIFIED))
    ap.add_argument("--repo", default=None)
    args = ap.parse_args(argv)
    clusters = json.loads(Path(args.clusters).read_text())
    rows = lane_select.load_rows(args.file)
    res = promote(clusters, rows, create_issue=lambda p: gh_create_issue(p, args.repo),
                  find_issue=lambda p: gh_find_issue(p, args.repo),
                  learnings_dir=Path(args.file).parent)
    print(json.dumps(res))
    for e in res["errors"]:
        print(f"warning: {e}", file=sys.stderr)
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
