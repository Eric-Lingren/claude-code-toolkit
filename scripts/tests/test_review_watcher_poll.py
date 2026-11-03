"""Tests for review-watcher poll.py. gh is stubbed with a fixture-printing script."""

import json
import os
import pathlib
import stat
import subprocess
import sys

import pytest

POLL = pathlib.Path(__file__).resolve().parents[1] / "review-watcher" / "poll.py"
ME = "Eric-Lingren"


def user(login):
    return {"__typename": "User", "login": login}


def pr(number, *, draft=False, author=None, reviewers=(ME,), events=None, sha="aaa", extra_nodes=()):
    author = author or user("alice")
    events = events if events is not None else [
        {"__typename": "ReviewRequestedEvent", "id": f"RRE_{number}",
         "createdAt": "2026-11-04T10:00:00Z", "requestedReviewer": user(ME)}]
    return {
        "number": number, "isDraft": draft, "headRefOid": sha, "url": f"u/{number}",
        "title": f"t{number}", "repository": {"nameWithOwner": "acme/app"},
        "author": author,
        "reviewRequests": {"nodes": [{"requestedReviewer": r if isinstance(r, dict) else user(r)}
                                     for r in reviewers]},
        "timelineItems": {"nodes": list(events) + list(extra_nodes)},
    }


@pytest.fixture
def env(tmp_path):
    fixture = tmp_path / "fixture.json"
    gh = tmp_path / "gh"
    gh.write_text(f"#!/bin/sh\ncat {fixture}\n")
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "home"
    e = dict(os.environ, REVIEW_WATCHER_HOME=str(home), REVIEW_WATCHER_GH=str(gh))

    def load(*prs):
        fixture.write_text(json.dumps(
            {"data": {"viewer": {"login": ME}, "search": {"nodes": list(prs)}}}))

    def run(*args):
        p = subprocess.run([sys.executable, str(POLL), *args], capture_output=True, text=True, env=e)
        assert p.returncode == 0, p.stderr
        return p.stdout

    def log():
        out = subprocess.run([sys.executable, str(POLL.parent / "events.py"), "log-path"],
                             capture_output=True, text=True, env=e).stdout.strip()
        try:
            with open(out) as fh:
                return [json.loads(l) for l in fh if l.strip()]
        except FileNotFoundError:
            return []

    return load, run, log


def test_logs_ready_direct_request_once(env):
    load, run, log = env
    load(pr(1))
    assert "request-seen acme/app#1 RRE_1" in run()
    assert run() == ""
    assert [e["request_id"] for e in log()] == ["RRE_1"]


def test_ignores_draft_bot_and_team_only(env):
    load, run, log = env
    team = {"__typename": "Team"}
    team_events = [{"__typename": "ReviewRequestedEvent", "id": "RRE_4",
                    "createdAt": "2026-11-04T10:00:00Z", "requestedReviewer": team}]
    load(
        pr(1, draft=True),
        pr(2, author={"__typename": "Bot", "login": "dependabot"}),
        pr(3, author=user("renovate[bot]")),
        pr(4, reviewers=[team], events=team_events),
        pr(5),
    )
    run()
    assert [e["pr"] for e in log()] == [5]


def test_approved_by_others_still_included(env):
    load, run, log = env
    load(pr(1, reviewers=(ME, "bob")))
    run()
    assert len(log()) == 1


def test_rerequest_same_head_logs_new_event_new_commit_does_not(env):
    load, run, log = env
    load(pr(1, sha="abc"))
    run()
    load(pr(1, sha="def"))  # new commit, no re-request
    assert run() == ""
    rerequest = [
        {"__typename": "ReviewRequestedEvent", "id": "RRE_1",
         "createdAt": "2026-11-04T10:00:00Z", "requestedReviewer": user(ME)},
        {"__typename": "ReviewRequestedEvent", "id": "RRE_1b",
         "createdAt": "2026-11-04T12:00:00Z", "requestedReviewer": user(ME)}]
    load(pr(1, sha="def", events=rerequest))
    assert "RRE_1b" in run()
    assert [e["request_id"] for e in log()] == ["RRE_1", "RRE_1b"]
    assert run() == ""


def test_effective_ts_is_later_ready_event(env):
    load, run, log = env
    ready = {"__typename": "ReadyForReviewEvent", "id": "R1", "createdAt": "2026-11-04T15:00:00Z"}
    load(pr(1, extra_nodes=[ready]))
    run()
    ev = log()[0]
    assert ev["ts"] == "2026-11-04T15:00:00Z"
    assert ev["data"]["requested_at"] == "2026-11-04T10:00:00Z"
    assert ev["data"]["ready_at"] == "2026-11-04T15:00:00Z"


def test_dry_run_writes_nothing_and_json(env):
    load, run, log = env
    load(pr(1))
    assert "would log" in run("--dry-run")
    assert log() == []
    out = json.loads(run("--json"))
    assert out[0]["request_id"] == "RRE_1"
    assert json.loads(run("--json")) == []
