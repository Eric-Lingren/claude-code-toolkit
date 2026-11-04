#!/usr/bin/env python3
# lane_select.py: pick the targets the learnings lane should work on next
# What: select(rows, now, open_prs) -> [(target, [learnings])]. Only confidence "confirmed" rows with a
#   target and status absent or "captured" count. A target is eligible with >= MIN_COUNT rows or an oldest
#   row >= MIN_AGE_DAYS old. Targets with an open lane PR, or named in skip (cooling down), wait. Order: oldest cluster first, then biggest.
#   Returns [] once open lane PRs reach OPEN_PR_CAP; otherwise at most the remaining slots.
# When: start of a lane pass. CLI: lane_select.py [--file PATH] prints JSON.
# Test hook: open_prs is an injectable callable returning the target of each open lane PR (no GitHub in tests).
import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Loaded by path under a unique name: review-watcher also has a top-level config.py.
_spec = importlib.util.spec_from_file_location("lane_config", HERE / "config.py")
config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(config)

UNIFIED = HERE.parent.parent / "learnings" / "unified-learnings.jsonl"


def load_rows(path=UNIFIED):
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _ts(row):
    return datetime.strptime(row["timestamp"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def is_eligible_row(row):
    return (row.get("confidence") == "confirmed" and bool(row.get("improves"))
            and row.get("status") in (None, "captured") and bool(row.get("timestamp")))


def gh_open_prs(cwd=None):
    """Targets of open lane PRs, one entry per PR, read via gh. Default for open_prs."""
    out = subprocess.run(
        ["gh", "pr", "list", "--state", "open", "--label", config.LANE_LABEL,
         "--json", "headRefName", "--limit", str(config.GH_LIST_LIMIT_OPEN_PRS)],
        capture_output=True, text=True, timeout=60, check=True, cwd=cwd).stdout
    pre = config.LANE_BRANCH_PREFIX
    return [p["headRefName"][len(pre):] if p["headRefName"].startswith(pre) else p["headRefName"]
            for p in json.loads(out)]


def select(rows, now=None, open_prs=gh_open_prs, skip=()):
    now = now or datetime.now(timezone.utc)
    open_list = list(open_prs())
    slots = config.OPEN_PR_CAP - len(open_list)
    if slots <= 0:
        return []
    waiting = set(open_list) | set(skip)
    groups = {}
    for r in rows:
        if is_eligible_row(r):
            groups.setdefault(r["improves"], []).append(r)
    cutoff = now - timedelta(days=config.MIN_AGE_DAYS)
    ranked = []
    for target, items in groups.items():
        if target in waiting:
            continue
        oldest = min(_ts(r) for r in items)
        if len(items) >= config.MIN_COUNT or oldest <= cutoff:
            ranked.append((oldest, -len(items), target, items))
    ranked.sort(key=lambda x: x[:3])
    return [(t, items) for _, _, t, items in ranked[:slots]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default=str(UNIFIED))
    args = ap.parse_args(argv)
    res = select(load_rows(args.file))
    print(json.dumps([{"target": t, "ids": [r.get("id") for r in items]} for t, items in res], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
