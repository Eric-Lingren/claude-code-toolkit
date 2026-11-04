#!/usr/bin/env python3
"""Per-skill correction rate per week.

What: rate = learnings that improve a skill / runs of that skill. Learnings come
  from unified-learnings.jsonl (timestamp + improves); runs are the summed `mix`
  counts across every per-host snapshots-<host>.json (cc-usage-benchmark.py's
  load_run_counts).
When: weekly quality check on skills; prints JSON for a target ISO week.

Rules:
  - A skill is alarm-eligible only with >= MIN_RUNS runs in its window.
  - The window starts at the target week alone and widens one week at a time
    (up to MAX_WEEKS, trailing) until it reaches MIN_RUNS. Still short at
    MAX_WEEKS -> reported with its rate but never alarmable.
  - Learnings with no `improves` are excluded from rates; they are reported as
    `unattributed`, counted over the widest (MAX_WEEKS) window.
  - hook-audit.jsonl is not used as a quality signal.

Usage:
  correction-rate.py [--week 2026-W45] [--report-dir DIR] [--learnings FILE]
"""
import argparse
import datetime
import importlib.util
import json
import os
import sys
from pathlib import Path

MIN_RUNS = 10
MAX_WEEKS = 4

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEARNINGS = os.path.join(HERE, "..", "..", "learnings",
                                 "unified-learnings.jsonl")


def iso_week(ts):
    """'2026-11-03T10:00:00Z' -> '2026-W45'; None when unparseable."""
    try:
        d = datetime.date.fromisoformat(str(ts)[:10])
    except ValueError:
        return None
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def weeks_back(week, n):
    """The n ISO weeks ending at `week`, newest first."""
    y, w = week.split("-W")
    monday = datetime.date.fromisocalendar(int(y), int(w), 1)
    out = []
    for i in range(n):
        yy, ww, _ = (monday - datetime.timedelta(weeks=i)).isocalendar()
        out.append(f"{yy}-W{ww:02d}")
    return out


def load_learnings(path):
    """Parsed rows of a jsonl file; missing file or bad lines are skipped."""
    rows = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def load_run_counts(report_dir=None):
    """{skill: {week: runs}} summed over every host's snapshot file."""
    spec = importlib.util.spec_from_file_location(
        "cc_usage_benchmark", os.path.join(HERE, "cc-usage-benchmark.py"))
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    return bench.load_run_counts(report_dir)


def compute(learnings, run_counts, week):
    """Correction rate per skill for the window ending at `week`."""
    horizon = weeks_back(week, MAX_WEEKS)
    per_skill = {}  # skill -> {week: learnings}
    unattributed = 0
    for row in learnings:
        wk = iso_week(row.get("timestamp"))
        if wk not in horizon:
            continue
        skill = row.get("improves")
        if not skill:
            unattributed += 1
            continue
        per_skill.setdefault(skill, {}).setdefault(wk, 0)
        per_skill[skill][wk] += 1

    skills = {}
    for skill in sorted(set(per_skill) | set(run_counts)):
        runs_by_wk = run_counts.get(skill, {})
        learn_by_wk = per_skill.get(skill, {})
        for n in range(1, MAX_WEEKS + 1):
            window = horizon[:n]
            runs = sum(runs_by_wk.get(w, 0) for w in window)
            if runs >= MIN_RUNS:
                break
        learned = sum(learn_by_wk.get(w, 0) for w in window)
        skills[skill] = {
            "learnings": learned,
            "runs": runs,
            "window_weeks": n,
            "rate": learned / runs if runs else None,
            "alarmable": runs >= MIN_RUNS,
        }
    return {"week": week, "skills": skills, "unattributed": unattributed}


def lane_metrics(learnings):
    """Learnings-lane health from write-back outcomes, per target and overall.

    merged = status applied with a merged_sha (set only by the lane write-back);
    rejected = status rejected; changes_requested = status changes_requested (Phase 3
    write-back; stays 0 until then). Rates are over the resolved total of the three.
    """
    def blank():
        return {"merged": 0, "rejected": 0, "changes_requested": 0}

    overall, by_target = blank(), {}
    for row in learnings:
        status = row.get("status")
        if status == "applied" and row.get("merged_sha"):
            key = "merged"
        elif status == "rejected":
            key = "rejected"
        elif status == "changes_requested":
            key = "changes_requested"
        else:
            continue
        overall[key] += 1
        by_target.setdefault(row.get("improves") or "unknown", blank())[key] += 1

    def finish(c):
        total = c["merged"] + c["rejected"] + c["changes_requested"]
        return {**c, "resolved": total,
                "merge_rate": c["merged"] / total if total else 0,
                "rejected_rate": c["rejected"] / total if total else 0,
                "changes_requested_rate": c["changes_requested"] / total if total else 0}

    out = finish(overall)
    out["by_target"] = {t: finish(c) for t, c in sorted(by_target.items())}
    return out


def render_lane_digest(metrics):
    """Kick digest lines for the lane rates (kick script prints these)."""
    return [
        "lane health: %d resolved" % metrics["resolved"],
        "  merge rate: %.2f (%d)" % (metrics["merge_rate"], metrics["merged"]),
        "  rejected rate: %.2f (%d)" % (metrics["rejected_rate"], metrics["rejected"]),
        "  changes-requested rate: %.2f (%d)" % (
            metrics["changes_requested_rate"], metrics["changes_requested"]),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=(
        "Per-skill correction rate (learnings / runs) for an ISO week. "
        "hook-audit.jsonl is not used as a quality signal."))
    today = datetime.date.today().isocalendar()
    ap.add_argument("--week", default=f"{today[0]}-W{today[1]:02d}")
    ap.add_argument("--report-dir", default=None,
                    help="dir holding snapshots-*.json (default: usage-reports)")
    ap.add_argument("--learnings", default=DEFAULT_LEARNINGS)
    args = ap.parse_args(argv)
    out = compute(load_learnings(args.learnings),
                  load_run_counts(args.report_dir), args.week)
    out["lane"] = lane_metrics(load_learnings(args.learnings))
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
