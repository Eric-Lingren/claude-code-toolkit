#!/usr/bin/env python3
"""Per-PR step timings and the pilot guard metric from the review watcher log.

Reads the event log through events.py and prints, for each PR request, the
elapsed office minutes between: request seen -> review started -> review ready
-> approved -> posted. A slow PR is attributable to the watcher (seen->started,
started->ready) or to the approval step (ready->approved, approved->posted).

Also prints the median review start -> posted time (the pilot guard metric) and
whether it stays under GUARD_MAX_OFFICE_MINUTES. All durations are office
minutes (weekdays inside office hours), consistent with the dashboard, even
though review work itself is wall-clock.

Usage: report.py [--repo R] [--pr N] [--json]
Test hook: REVIEW_WATCHER_HOME relocates the log.
"""
import argparse
import json
import statistics
import sys

import events
from office_minutes import office_minutes

# Pilot guard: median review-start-to-posted must stay under this many minutes.
GUARD_MAX_OFFICE_MINUTES = 30

# (label, from-field, to-field), in pipeline order.
STEPS = (
    ("seen_to_started", "requested_at", "started_at"),
    ("started_to_ready", "started_at", "ready_at"),
    ("ready_to_approved", "ready_at", "approved_at"),
    ("approved_to_posted", "approved_at", "posted_at"),
)


def _gap(entry, a, b):
    if not entry.get(a) or not entry.get(b):
        return None
    return office_minutes(events.parse_ts(entry[a]), events.parse_ts(entry[b]))


def build(entries):
    rows = []
    for e in entries:
        row = {"repo": e["repo"], "pr": e["pr"], "request_id": e["request_id"],
               "state": e["state"]}
        for label, a, b in STEPS:
            row[label] = _gap(e, a, b)
        row["start_to_posted"] = _gap(e, "started_at", "posted_at")
        rows.append(row)
    vals = [r["start_to_posted"] for r in rows if r["start_to_posted"] is not None]
    median = statistics.median(vals) if vals else None
    guard = None if median is None else ("PASS" if median < GUARD_MAX_OFFICE_MINUTES else "FAIL")
    return {"unit": "office_minutes", "requests": rows,
            "median_start_to_posted": median, "posted_count": len(vals),
            "guard_max_office_minutes": GUARD_MAX_OFFICE_MINUTES, "guard": guard}


def _fmt(v):
    return "-" if v is None else f"{v}m"


def render(report):
    lines = ["Durations are office minutes (weekdays within office hours).", ""]
    if not report["requests"]:
        lines.append("(no requests)")
    for r in report["requests"]:
        lines.append(f"{r['repo']}#{r['pr']}  {r['request_id']}  [{r['state']}]")
        lines.append(
            "  seen->started {}  started->ready {}  ready->approved {}  approved->posted {}".format(
                *(_fmt(r[l]) for l, _, _ in STEPS)))
        lines.append(f"  start->posted {_fmt(r['start_to_posted'])}")
    lines.append("")
    m = report["median_start_to_posted"]
    if m is None:
        lines.append("Median review start -> posted: n/a (no posted reviews)")
    else:
        lines.append(f"Median review start -> posted: {m:g}m over {report['posted_count']} PR(s)"
                     f" - guard {report['guard']} (< {report['guard_max_office_minutes']}m)")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Review watcher step timings report")
    p.add_argument("--repo")
    p.add_argument("--pr", type=int)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    entries = events.reduce_events(events.read())
    entries = [e for e in entries
               if (not args.repo or e["repo"] == args.repo)
               and (args.pr is None or e["pr"] == args.pr)]
    report = build(entries)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
