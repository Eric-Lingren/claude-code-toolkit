"""Regenerates sprint.json and pool.json next to this file."""
import json, os

D = os.path.dirname(os.path.abspath(__file__))


def n(i, t, proj, est, created):
    return {"identifier": i, "title": t, "url": "https://linear.app/x/" + i, "estimate": est,
            "createdAt": created, "branchName": "eric/" + i.lower(),
            "state": {"name": "Backlog", "type": "backlog"},
            "project": {"name": proj} if proj else None, "parent": None, "assignee": None,
            "labels": {"nodes": []}, "attachments": {"nodes": []}, "inverseRelations": {"nodes": []}}


json.dump({"issues": {"nodes": [n("KEY-1", "mine a", "Exports", 2, "2026-09-01T00:00:00Z"),
                                n("KEY-2", "mine b", "Billing", 1, "2026-09-02T00:00:00Z"),
                                n("KEY-3", "mine c", "Exports", 3, "2026-09-03T00:00:00Z")]}},
          open(os.path.join(D, "sprint.json"), "w"), indent=1)
json.dump({"issues": {"nodes": [n("KEY-10", "small export", "Exports", 1, "2026-09-01T00:00:00Z"),
                                n("KEY-11", "no project", "", 2, "2026-09-02T00:00:00Z"),
                                n("KEY-12", "big billing", "Billing", 5, "2026-09-03T00:00:00Z")]}},
          open(os.path.join(D, "pool.json"), "w"), indent=1)
