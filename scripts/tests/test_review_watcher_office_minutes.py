"""Tests for review-watcher office-minute helper and active window."""

import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

WATCHER = pathlib.Path(__file__).resolve().parents[1] / "review-watcher"
sys.path.insert(0, str(WATCHER))

import office_minutes  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")


def pt(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=PT)


# 2026-11-06 is a Friday, 2026-11-09 the following Monday.


def test_friday_5pm_to_monday_10am_is_120_minutes():
    assert office_minutes.office_minutes(pt(2026, 11, 6, 17), pt(2026, 11, 9, 10)) == 120


def test_same_day_inside_office_hours():
    assert office_minutes.office_minutes(pt(2026, 11, 4, 10), pt(2026, 11, 4, 11, 30)) == 90


def test_before_open_and_after_close_are_clipped():
    assert office_minutes.office_minutes(pt(2026, 11, 4, 7), pt(2026, 11, 4, 20)) == 9 * 60


def test_weekend_only_span_is_zero():
    assert office_minutes.office_minutes(pt(2026, 11, 7, 9), pt(2026, 11, 8, 18)) == 0


def test_multi_day_span_sums_full_days():
    # Tue 4pm -> Thu 10am = 120 (Tue) + 540 (Wed) + 60 (Thu)
    assert office_minutes.office_minutes(pt(2026, 11, 3, 16), pt(2026, 11, 5, 10)) == 720


def test_reversed_span_is_zero():
    assert office_minutes.office_minutes(pt(2026, 11, 4, 12), pt(2026, 11, 4, 10)) == 0


def test_accepts_utc_inputs():
    start = pt(2026, 11, 4, 10).astimezone(ZoneInfo("UTC"))
    end = pt(2026, 11, 4, 11).astimezone(ZoneInfo("UTC"))
    assert office_minutes.office_minutes(start, end) == 60


def test_active_window_is_weekdays_850_to_1800():
    assert office_minutes.in_active_window(pt(2026, 11, 4, 8, 49)) is False
    assert office_minutes.in_active_window(pt(2026, 11, 4, 8, 50)) is True
    assert office_minutes.in_active_window(pt(2026, 11, 4, 17, 59)) is True
    assert office_minutes.in_active_window(pt(2026, 11, 4, 18, 0)) is False
    assert office_minutes.in_active_window(pt(2026, 11, 7, 12, 0)) is False
