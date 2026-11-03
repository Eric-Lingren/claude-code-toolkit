#!/usr/bin/env python3
"""Ensure the pinned "Review Watcher" cmux workspace exists at the top of the sidebar.

ensure()  (called each watcher tick, before alerts)
  - missing: `cmux new-workspace --command <queue-view.sh>`, rename to Review Watcher,
    pin, move to index 0.
  - present under a legacy title ("Reviews"): rename it to Review Watcher first.
  - present but not first in `cmux list-workspaces`: move to index 0 and pin.
  - present and first: no-op.
Reuses alerts.cmux (short timeout, never raises) and alerts.reviews_workspace. If the
socket is unreachable every call returns None and ensure() does nothing.

CLI:  workspace.py ensure
"""
import os
import re
import sys

import alerts

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_VIEW = os.path.join(HERE, "queue-view.sh")
TITLE = alerts.WORKSPACE_TITLE
_REF = re.compile(r"workspace:\d+")


def _refs(listing):
    return [m.group(0) for m in (_REF.search(l) for l in listing.splitlines()) if m]


def ensure():
    """Returns "noop", "created", "moved" or "unreachable"."""
    listing = alerts.cmux("list-workspaces")
    if listing is None:
        return "unreachable"
    ws = alerts.workspace_titled(TITLE, listing)
    if not ws:
        for legacy in alerts.LEGACY_WORKSPACE_TITLES:
            ws = alerts.workspace_titled(legacy, listing)
            if ws:
                alerts.cmux("rename-workspace", "--workspace", ws, TITLE)
                break
    if ws:
        refs = _refs(listing)
        if refs and refs[0] == ws:
            return "noop"
        alerts.cmux("reorder-workspace", "--workspace", ws, "--index", "0")
        alerts.cmux("workspace-action", "--action", "pin", "--workspace", ws)
        return "moved"
    out = alerts.cmux("new-workspace", "--command", QUEUE_VIEW)
    if out is None:
        return "unreachable"
    m = _REF.search(out)
    if not m:
        return "unreachable"
    ws = m.group(0)
    alerts.cmux("rename-workspace", "--workspace", ws, TITLE)
    alerts.cmux("workspace-action", "--action", "pin", "--workspace", ws)
    alerts.cmux("reorder-workspace", "--workspace", ws, "--index", "0")
    return "created"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ["ensure"]:
        print("usage: workspace.py ensure", file=sys.stderr)
        return 2
    print(ensure())
    return 0


if __name__ == "__main__":
    sys.exit(main())
