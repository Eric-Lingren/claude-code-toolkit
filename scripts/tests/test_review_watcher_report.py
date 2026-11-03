"""Tests for the review-watcher report (per-step timings, guard median).

The report only reads the event log; no external calls (gh, cmux,
terminal-notifier, claude) are involved, so there is nothing to stub.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

WATCHER = pathlib.Path(__file__).resolve().parents[1] / "review-watcher"
EVENTS = WATCHER / "events.py"
REPORT = WATCHER / "report.py"
# 2026-11-04 is a Wednesday; all times inside office hours.
D = "2026-11-04T"
TZ = "-08:00"

# repo, pr, request id, then (seen, started, ready, approved, posted) clock times
SAMPLE = [
    ("acme/app", 1, "a", ("09:10", "09:20", "09:50", "10:30", "10:35")),   # start->posted 75
    ("acme/app", 2, "b", ("10:00", "10:05", "10:15", "10:20", "10:25")),   # 20
    ("acme/web", 3, "c", ("11:00", "11:30", "11:40", "12:00", "12:10")),   # 40
    ("acme/web", 4, "d", ("13:00", "13:10", "13:30", None, None)),         # not posted
]
TYPES = ("request_seen", "review_started", "review_ready", "approved", "posted")


@pytest.fixture
def home(tmp_path):
    env = dict(os.environ, REVIEW_WATCHER_HOME=str(tmp_path))
    for repo, pr, rid, times in SAMPLE:
        for etype, t in zip(TYPES, times):
            if t is None:
                continue
            subprocess.run(
                [sys.executable, str(EVENTS), "append", etype, "--repo", repo,
                 "--pr", str(pr), "--request-id", rid, "--ts", f"{D}{t}:00{TZ}"],
                check=True, capture_output=True, env=env)
    return tmp_path


def report(home, *args):
    env = dict(os.environ, REVIEW_WATCHER_HOME=str(home))
    proc = subprocess.run([sys.executable, str(REPORT), *args],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def test_per_pr_step_durations(home):
    data = json.loads(report(home, "--json"))
    rows = {r["pr"]: r for r in data["requests"]}
    assert len(rows) == 4
    steps = ("seen_to_started", "started_to_ready", "ready_to_approved", "approved_to_posted")
    assert tuple(rows[1][s] for s in steps) == (10, 30, 40, 5)
    assert tuple(rows[2][s] for s in steps) == (5, 10, 5, 5)
    # unreached steps are null, not zero
    assert rows[4]["ready_to_approved"] is None and rows[4]["start_to_posted"] is None


def test_text_output_shows_each_pr_and_office_minutes_header(home):
    out = report(home)
    assert "office minutes" in out
    for ref in ("acme/app#1", "acme/app#2", "acme/web#3", "acme/web#4"):
        assert ref in out
    assert "seen->started 10m  started->ready 30m  ready->approved 40m  approved->posted 5m" in out


def test_median_start_to_posted_matches_hand_calculation(home):
    # start->posted: 09:20->10:35 = 75, 10:05->10:25 = 20, 11:30->12:10 = 40
    # sorted 20, 40, 75 -> median 40 (unposted PR 4 excluded)
    data = json.loads(report(home, "--json"))
    assert data["median_start_to_posted"] == 40
    assert data["posted_count"] == 3
    assert data["guard"] == "FAIL"  # 40 >= 30
    assert "Median review start -> posted: 40m over 3 PR(s) - guard FAIL" in report(home)


def test_guard_passes_under_30(home):
    out = json.loads(report(home, "--json", "--repo", "acme/app", "--pr", "2"))
    assert out["median_start_to_posted"] == 20
    assert out["guard"] == "PASS"


def test_empty_log(tmp_path):
    out = report(tmp_path)
    assert "(no requests)" in out and "n/a" in out
