#!/usr/bin/env python3
"""Teammate-overlap filter for /pick (Linear candidates). Read-only, shared by all buckets.

apply(doc, activity) -> doc copy where:
  - a candidate whose `parent` matches the parent of any teammate-active ticket
    (a different ticket) is hard-excluded;
  - otherwise, a candidate whose `project` matches a teammate-active ticket's
    project gets a note '👥 <name> active in <project>'. Rank is untouched.

activity: list of {id, parent, project, assignee} for tickets teammates have
in progress (from pick-fetch-linear-activity.sh). Entries without an assignee
are ignored. Tickets with no parent/project never match.

CLI: overlap.py <cands.json> <activity.json>  -> filtered candidate JSON on stdout.
"""
import json, sys


def apply(doc, activity):
    act = [a for a in activity if a.get("assignee")]
    out = []
    for c in doc["candidates"]:
        others = [a for a in act if a.get("id") != c["id"]]
        if c.get("parent") and any(a.get("parent") == c["parent"] for a in others):
            continue
        notes = list(c.get("notes", []))
        if c.get("project"):
            for name in sorted({a["assignee"] for a in others if a.get("project") == c["project"]}):
                notes.append(f"👥 {name} active in {c['project']}")
        out.append({**c, "notes": notes} if notes else c)
    return {**doc, "candidates": out}


if __name__ == "__main__":
    doc = json.load(open(sys.argv[1]))
    print(json.dumps(apply(doc, json.load(open(sys.argv[2]))), indent=2))
