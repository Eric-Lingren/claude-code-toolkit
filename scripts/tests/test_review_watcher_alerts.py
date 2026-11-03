"""Tests for review-watcher alerts (pill, banner, reminder, failure notice, buttons).

gh, cmux and notify.sh are stub binaries that record argv; claude is never
reached (events are written directly). Nothing real is called, no real banner.
"""

import json
import os
import pathlib
import stat
import subprocess
import sys
import time

import pytest

WATCHER_DIR = pathlib.Path(__file__).resolve().parents[1] / "review-watcher"
WATCHER = WATCHER_DIR / "watcher.sh"
EVENTS = WATCHER_DIR / "events.py"
ALERTS = WATCHER_DIR / "alerts.py"
REPO = "acme/app"
SEEN = "2026-11-04T09:00:00-08:00"
READY = "2026-11-04T09:30:00-08:00"
TICK1 = "2026-11-04T09:40:00-08:00"
TICK_REMIND = "2026-11-04T10:45:00-08:00"  # 75 office minutes after READY
TICK_LATER = "2026-11-04T11:30:00-08:00"

GH = """#!/bin/sh
case "$1 $2" in
  "api graphql") echo '{"data":{"viewer":{"login":"me"},"search":{"nodes":[]}}}' ;;
  "api user") echo me ;;
  "pr view") echo '{"reviews":[]}' ;;
esac
"""

CMUX = """#!/bin/bash
echo "$*" >> "$FAKE_CMUX_LOG"
case "${FAKE_CMUX_MODE:-ok}" in
  fail) exit 1 ;;
  hang) sleep 30; exit 0 ;;
esac
[ "$1" = "list-workspaces" ] && echo "* workspace:7  Reviews"
exit 0
"""

# Fake alerter: records argv, answers with $FAKE_ALERTER_ANSWER.
ALERTER = """#!/bin/bash
printf '%s\\n' "$*" >> "$FAKE_ALERTER_LOG"
echo "${FAKE_ALERTER_ANSWER:-@CONTENTCLICKED}"
"""

NOTIFY = """#!/bin/bash
printf '%s\\n' "$*" >> "$FAKE_NOTIFY_LOG"
exit 0
"""


def executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class Rig:
    def __init__(self, tmp):
        self.home = tmp / "home"
        self.cmux_log = tmp / "cmux.log"
        self.notify_log = tmp / "notify.log"
        self.cmux_log.write_text("")
        self.notify_log.write_text("")
        self.env = dict(
            os.environ, REVIEW_WATCHER_HOME=str(self.home),
            REVIEW_WATCHER_GH=str(executable(tmp / "gh", GH)),
            REVIEW_WATCHER_CMUX=str(executable(tmp / "cmux", CMUX)),
            REVIEW_WATCHER_NOTIFY=str(executable(tmp / "notify", NOTIFY)),
            REVIEW_WATCHER_ALERTER="",
            REVIEW_WATCHER_CHECKOUTS=json.dumps({}),
            FAKE_CMUX_LOG=str(self.cmux_log), FAKE_NOTIFY_LOG=str(self.notify_log),
        )

    def event(self, etype, n, rid, ts, data=None):
        args = [sys.executable, str(EVENTS), "append", etype, "--repo", REPO, "--pr", str(n),
                "--request-id", rid, "--ts", ts]
        if data:
            args += ["--data", json.dumps(data)]
        subprocess.run(args, env=self.env, capture_output=True, text=True, check=True)

    def ready(self, n, rid, title=None):
        self.event("request_seen", n, rid, SEEN, {"title": title or f"title {n}"})
        self.event("review_ready", n, rid, READY)

    def tick(self, now=TICK1, **extra):
        env = dict(self.env, REVIEW_WATCHER_NOW=now, **extra)
        return subprocess.run(["bash", str(WATCHER)], capture_output=True, text=True,
                              env=env, timeout=60)

    def banners(self):
        return [l for l in self.notify_log.read_text().splitlines() if l.strip()]

    def cmux_calls(self):
        return [l for l in self.cmux_log.read_text().splitlines() if l.strip()]

    def states(self):
        p = subprocess.run([sys.executable, str(EVENTS), "state", "--json"],
                           capture_output=True, text=True, env=self.env, check=True)
        return {e["request_id"]: e["state"] for e in json.loads(p.stdout)}


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_one_ready_sets_pill_and_one_banner(rig):
    rig.ready(11, "r1", "Fix the thing")
    assert rig.tick().returncode == 0
    assert any("set-status reviews 1 ready" in c and "--workspace workspace:7" in c
               for c in rig.cmux_calls())
    banners = rig.banners()
    assert len(banners) == 1
    assert "PR #11 review ready: Fix the thing" in banners[0]
    assert "--group reviews" in banners[0]
    assert "select-workspace --workspace workspace:7" in banners[0]
    # second tick: same readiness, no new banner
    rig.tick()
    assert len(rig.banners()) == 1


def test_two_ready_in_one_tick_batch(rig):
    rig.ready(11, "r1")
    rig.ready(12, "r2")
    rig.tick()
    banners = rig.banners()
    assert len(banners) == 1
    assert "2 reviews ready" in banners[0]
    assert any("set-status reviews 2 ready" in c for c in rig.cmux_calls())


def test_reminder_fires_once_after_60_office_minutes(rig):
    rig.ready(11, "r1")
    rig.tick(TICK1)
    assert len(rig.banners()) == 1
    rig.tick(TICK_REMIND)
    assert len(rig.banners()) == 2
    assert "still waiting" in rig.banners()[1]
    assert rig.states()["r1"] == "reminded"
    rig.tick(TICK_LATER)
    assert len(rig.banners()) == 2
    reminders = [e for e in (json.loads(l) for l in
                 (rig.home / "events.jsonl").read_text().splitlines()) if e["type"] == "reminded"]
    assert len(reminders) == 1


def test_no_reminder_before_60_minutes(rig):
    rig.ready(11, "r1")
    rig.tick(TICK1)
    rig.tick("2026-11-04T10:00:00-08:00")
    assert len(rig.banners()) == 1


def test_cmux_unreachable_tick_completes_and_banner_fires(rig):
    rig.ready(11, "r1")
    res = rig.tick(FAKE_CMUX_MODE="fail")
    assert res.returncode == 0
    assert len(rig.banners()) == 1
    assert "open -a cmux" in rig.banners()[0]
    assert "select-workspace" not in rig.banners()[0]


def test_cmux_hang_does_not_block_tick(rig):
    rig.ready(11, "r1")
    res = rig.tick(FAKE_CMUX_MODE="hang")
    assert res.returncode == 0
    assert len(rig.banners()) == 1


def test_failed_run_banner_once(rig):
    rig.event("request_seen", 11, "r1", SEEN, {"title": "t"})
    rig.event("failed", 11, "r1", READY, {"reason": "timeout"})
    rig.tick()
    banners = rig.banners()
    assert len(banners) == 1
    assert "Review failed: PR #11 (timeout)" in banners[0]
    rig.tick()
    assert len(rig.banners()) == 1


def test_pill_clears_when_queue_empties(rig):
    rig.ready(11, "r1")
    rig.tick()
    rig.event("posted", 11, "r1", "2026-11-04T09:50:00-08:00")
    rig.tick("2026-11-04T09:55:00-08:00")
    assert any(c.startswith("clear-status reviews") and "--workspace workspace:7" in c
               for c in rig.cmux_calls())


def test_pill_cli_updates_pill_only(rig):
    rig.ready(11, "r1")
    res = subprocess.run([sys.executable, str(ALERTS), "pill"], env=rig.env,
                         capture_output=True, text=True)
    assert res.returncode == 0
    assert any("set-status reviews 1 ready" in c for c in rig.cmux_calls())
    assert rig.banners() == []


# --- clickable alerter banner ---------------------------------------------------------


def wait_for(pred, seconds=15):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.2)
    return pred()


def button_env(rig, tmp_path, answer):
    log = tmp_path / "alerter.log"
    log.write_text("")
    rig.env.update(REVIEW_WATCHER_ALERTER=str(executable(tmp_path / "alerter", ALERTER)),
                   FAKE_ALERTER_LOG=str(log), FAKE_ALERTER_ANSWER=answer,
                   REVIEW_WATCHER_OPEN_APP="/usr/bin/true")
    return log


def requests(rig):
    d = rig.home / "open-requests"
    return [p.read_text() for p in sorted(d.iterdir())] if d.exists() else []


def test_ready_banner_click_queues_request(rig, tmp_path):
    log = button_env(rig, tmp_path, "@CONTENTCLICKED")
    rig.ready(11, "r1", "Fix the thing")
    assert rig.tick().returncode == 0
    assert wait_for(lambda: requests(rig) == ["11"])
    call = log.read_text()
    assert "--actions Open review" in call and "--close-label Later" in call
    assert "Click to open the review, or use Options." in call
    assert "--group reviews" in call and "Fix the thing" in call
    assert rig.banners() == []  # no plain banner on top


def test_open_review_option_queues_request(rig, tmp_path):
    button_env(rig, tmp_path, "Open review")
    rig.ready(11, "r1")
    rig.tick()
    assert wait_for(lambda: requests(rig) == ["11"])


@pytest.mark.parametrize("answer", ["Later", "@TIMEOUT"])
def test_later_and_timeout_queue_nothing(rig, tmp_path, answer):
    log = button_env(rig, tmp_path, answer)
    rig.ready(11, "r1")
    rig.tick()
    assert wait_for(lambda: log.read_text().strip())
    time.sleep(1)
    assert requests(rig) == []


def test_close_queues_nothing(rig, tmp_path):
    log = button_env(rig, tmp_path, "@CLOSED")
    rig.ready(11, "r1")
    rig.tick()
    assert wait_for(lambda: log.read_text().strip())
    time.sleep(1)
    assert requests(rig) == []


def test_several_ready_open_the_oldest(rig, tmp_path):
    button_env(rig, tmp_path, "@CONTENTCLICKED")
    rig.ready(11, "r1")
    rig.ready(12, "r2")
    rig.tick()
    assert wait_for(lambda: requests(rig) == [""])


def test_failure_banner_stays_plain(rig, tmp_path):
    log = button_env(rig, tmp_path, "@CONTENTCLICKED")
    rig.event("request_seen", 11, "r1", SEEN)
    rig.event("failed", 11, "r1", READY, {"reason": "boom"})
    rig.tick()
    assert any("Review failed" in b for b in rig.banners())
    time.sleep(1)
    assert log.read_text() == ""


def test_open_requests_drop_stale_and_keep_order(rig, monkeypatch):
    monkeypatch.syspath_prepend(str(WATCHER_DIR))
    monkeypatch.setenv("REVIEW_WATCHER_HOME", str(rig.home))
    import importlib, config, open_requests
    importlib.reload(config)
    importlib.reload(open_requests)
    open_requests.add(11, now=1000.0)
    open_requests.add(None, now=1500.0)
    open_requests.add(12, now=2000.0)
    assert open_requests.take(now=2100.0) == ["", "12"]  # 11 is older than 600s
    assert open_requests.take(now=2100.0) == []
