#!/usr/bin/env python3
"""Office-minute arithmetic for the review watcher.

Office minutes count only weekdays inside the config's office hours, in the
config's timezone. Also exposes the watcher's active-window check.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config

TZ = ZoneInfo(config.TIMEZONE)


def _at(day, t):
    return datetime.combine(day, t, tzinfo=TZ)


def office_minutes(start, end):
    """Whole office minutes between two aware datetimes (0 if end <= start)."""
    start, end = start.astimezone(TZ), end.astimezone(TZ)
    if end <= start:
        return 0
    total = timedelta()
    day = start.date()
    while day <= end.date():
        if day.weekday() in config.WEEKDAYS:
            lo = max(start, _at(day, config.OFFICE_HOURS_START))
            hi = min(end, _at(day, config.OFFICE_HOURS_END))
            if hi > lo:
                total += hi - lo
        day += timedelta(days=1)
    return int(total.total_seconds() // 60)


def in_active_window(now):
    """True when `now` is inside the watcher's weekday active window."""
    now = now.astimezone(TZ)
    if now.weekday() not in config.WEEKDAYS:
        return False
    return config.ACTIVE_WINDOW_START <= now.time() < config.ACTIVE_WINDOW_END
