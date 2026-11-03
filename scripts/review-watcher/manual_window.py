#!/usr/bin/env python3
"""After-hours window opened by a manual command (review-rerun).

Outside ACTIVE_WINDOW the watcher tick is a silent no-op, so a rerun typed at
night would sit queued until morning. A manual command writes an ISO timestamp
to STATE_DIR/manual-until (now + MANUAL_RUN_MINUTES); while it is in the future
the watcher ticks as if in office hours, so the run starts, its banner fires and
it is supervised. Opening again only ever extends the window.
"""
import os
from datetime import timedelta

import config
import events


def path():
    return os.path.join(config.STATE_DIR, "manual-until")


def until():
    """The window's end as a datetime, or None when it was never opened or is unreadable."""
    try:
        with open(path(), encoding="utf-8") as fh:
            return events.parse_ts(fh.read().strip())
    except (OSError, ValueError):
        return None


def is_open(at):
    end = until()
    return end is not None and at < end


def extend(now):
    """Keep the watcher on until now + MANUAL_RUN_MINUTES. Returns the end."""
    end = now + timedelta(minutes=config.MANUAL_RUN_MINUTES)
    current = until()
    if current is not None and current > end:
        return current
    os.makedirs(config.STATE_DIR, exist_ok=True)
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(end.isoformat())
    os.replace(tmp, path())
    return end
