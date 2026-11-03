"""Tests for the review-watcher event log, queue reducer and CLI.

No external calls (gh, cmux, terminal-notifier, claude) are made by this
layer; the CLI is exercised end to end against a temp state directory.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

WATCHER = pathlib.Path(__file__).resolve().parents[1] / "review-watcher"
EVENTS = WATCHER / "events.py"
CONFIG = WATCHER / "config.py"
PR = ["--repo", "acme/app", "--pr", "42"]
# 2026-11-04 is a Wednesday.
T0 = "2026-11-04T10:00:00-08:00"


@pytest.fixture
def home(tmp_path):
    return tmp_path


def run(home, *args, expect=0):
    env = dict(os.environ, REVIEW_WATCHER_HOME=str(home))
    proc = subprocess.run(
        [sys.executable, str(EVENTS), *args], capture_output=True, text=True, env=env
    )
    assert proc.returncode == expect, proc.stdout + proc.stderr
    return proc.stdout


def append(home, etype, *extra, ts=None, expect=0):
    args = ["append", etype, *PR, *extra]
    if ts:
        args += ["--ts", ts]
    return run(home, *args, expect=expect)


def state(home, *extra, now="2026-11-04T11:00:00-08:00"):
    return json.loads(run(home, "state", "--json", "--now", now, *extra))


def test_request_started_ready_reduces_to_ready_with_wait(home):
    append(home, "request_seen", "--request-id", "r1", ts=T0)
    append(home, "review_started", "--request-id", "r1", ts="2026-11-04T10:05:00-08:00")
    append(home, "review_ready", "--request-id", "r1", ts="2026-11-04T10:25:00-08:00")

    entries = state(home)
    assert len(entries) == 1
    e = entries[0]
    assert (e["repo"], e["pr"], e["request_id"]) == ("acme/app", 42, "r1")
    assert e["state"] == "ready"
    assert e["wait_office_minutes"] == 60  # 10:00 -> now 11:00, still waiting on Eric


def test_text_output_lists_pr_state_and_wait(home):
    append(home, "request_seen", "--request-id", "r1", ts=T0)
    append(home, "review_started", "--request-id", "r1", ts=T0)
    append(home, "review_ready", "--request-id", "r1", ts=T0)
    out = run(home, "state", "--now", "2026-11-04T11:00:00-08:00")
    assert "acme/app#42" in out and "ready" in out and "60m" in out


def test_two_requests_same_pr_same_head_are_two_entries(home):
    append(home, "request_seen", "--request-id", "r1", "--head-sha", "abc123", ts=T0)
    append(home, "request_seen", "--request-id", "r2", "--head-sha", "abc123",
           ts="2026-11-04T10:30:00-08:00")
    entries = state(home)
    assert sorted(e["request_id"] for e in entries) == ["r1", "r2"]
    assert {e["head_sha"] for e in entries} == {"abc123"}
    assert {e["state"] for e in entries} == {"queued"}


def test_repeated_request_seen_with_same_id_is_idempotent(home):
    append(home, "request_seen", "--request-id", "r1", ts=T0)
    out = append(home, "request_seen", "--request-id", "r1", ts=T0)
    assert "exists" in out
    assert len(state(home)) == 1
    assert len((pathlib.Path(home) / "events.jsonl").read_text().splitlines()) == 1


def test_request_without_id_gets_a_unique_generated_id(home):
    a = append(home, "request_seen", ts=T0).strip()
    b = append(home, "request_seen", ts=T0).strip()
    assert a and b and a != b
    assert len(state(home)) == 2


def test_later_events_without_request_id_attach_to_latest_request_for_pr(home):
    append(home, "request_seen", "--request-id", "r1", ts=T0)
    append(home, "request_seen", "--request-id", "r2", ts="2026-11-04T10:10:00-08:00")
    append(home, "review_started", ts="2026-11-04T10:12:00-08:00")
    by_id = {e["request_id"]: e for e in state(home)}
    assert by_id["r1"]["state"] == "queued"
    assert by_id["r2"]["state"] == "running"


def test_event_without_any_request_fails(home):
    out = append(home, "review_started", expect=1)
    assert out == ""


def test_queue_states_running_reminded_failed(home):
    for rid in ("a", "b", "c", "d"):
        run(home, "append", "request_seen", "--repo", "acme/app", "--pr", str(ord(rid)),
            "--request-id", rid, "--ts", T0)
    def ev(rid, etype, **kw):
        run(home, "append", etype, "--repo", "acme/app", "--pr", str(ord(rid)),
            "--request-id", rid, "--ts", "2026-11-04T10:10:00-08:00",
            *(["--reason", kw["reason"]] if "reason" in kw else []))
    ev("b", "review_started")
    ev("c", "review_started"); ev("c", "review_ready"); ev("c", "reminded")
    ev("d", "review_started"); ev("d", "failed", reason="timeout")
    by_id = {e["request_id"]: e for e in state(home)}
    assert by_id["a"]["state"] == "queued"
    assert by_id["b"]["state"] == "running"
    assert by_id["c"]["state"] == "reminded"
    assert by_id["d"]["state"] == "failed"
    assert by_id["d"]["failed_reason"] == "timeout"


def test_posted_and_failed_stop_accruing_wait(home):
    append(home, "request_seen", "--request-id", "r1", ts=T0)
    append(home, "review_started", "--request-id", "r1", ts=T0)
    append(home, "review_ready", "--request-id", "r1", ts=T0)
    append(home, "approved", "--request-id", "r1", ts="2026-11-04T10:20:00-08:00")
    append(home, "posted", "--request-id", "r1", ts="2026-11-04T10:30:00-08:00")
    e = state(home, now="2026-11-05T15:00:00-08:00")[0]
    assert e["state"] == "posted"
    assert e["wait_office_minutes"] == 30


def test_wait_counts_office_minutes_only(home):
    # Friday 5pm request, read Monday 10am: 120 office minutes.
    append(home, "request_seen", "--request-id", "r1", ts="2026-11-06T17:00:00-08:00")
    e = state(home, now="2026-11-09T10:00:00-08:00")[0]
    assert e["wait_office_minutes"] == 120


def test_tool_denied_is_recorded_and_counted(home):
    append(home, "request_seen", "--request-id", "r1", ts=T0)
    append(home, "tool_denied", "--request-id", "r1", "--tool", "Bash", ts=T0)
    append(home, "tool_denied", "--request-id", "r1", "--tool", "Edit", ts=T0)
    e = state(home)[0]
    assert e["denied_tool_calls"] == 2
    lines = [json.loads(l) for l in (pathlib.Path(home) / "events.jsonl").read_text().splitlines()]
    assert [l["data"].get("tool") for l in lines if l["type"] == "tool_denied"] == ["Bash", "Edit"]


def test_filters_by_repo_pr_and_state(home):
    append(home, "request_seen", "--request-id", "r1", ts=T0)
    run(home, "append", "request_seen", "--repo", "acme/other", "--pr", "7",
        "--request-id", "r2", "--ts", T0)
    assert [e["request_id"] for e in state(home, "--repo", "acme/other")] == ["r2"]
    assert [e["request_id"] for e in state(home, "--pr", "42")] == ["r1"]
    assert state(home, "--state", "ready") == []


def test_state_write_snapshots_to_state_path(home):
    append(home, "request_seen", "--request-id", "r1", ts=T0)
    run(home, "state", "--write", "--now", "2026-11-04T11:00:00-08:00")
    snap = json.loads((pathlib.Path(home) / "state.json").read_text())
    assert snap[0]["request_id"] == "r1"


def test_extra_data_is_stored_and_merged_into_entry(home):
    append(home, "request_seen", "--request-id", "r1", "--data",
           '{"title": "Fix bug", "author": "sam"}', ts=T0)
    e = state(home)[0]
    assert e["title"] == "Fix bug" and e["author"] == "sam"


def test_log_is_append_only_jsonl_with_required_fields(home):
    append(home, "request_seen", "--request-id", "r1", ts=T0)
    append(home, "review_started", "--request-id", "r1", ts=T0)
    lines = (pathlib.Path(home) / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert {"ts", "type", "repo", "pr", "request_id"} <= first.keys()


def test_unknown_event_type_is_rejected(home):
    proc = subprocess.run(
        [sys.executable, str(EVENTS), "append", "bogus", *PR],
        capture_output=True, text=True, env=dict(os.environ, REVIEW_WATCHER_HOME=str(home)),
    )
    assert proc.returncode == 2


def test_library_read_returns_events_in_order(home, monkeypatch):
    monkeypatch.setenv("REVIEW_WATCHER_HOME", str(home))
    append(home, "request_seen", "--request-id", "r1", ts=T0)
    append(home, "review_started", "--request-id", "r1", ts=T0)
    monkeypatch.syspath_prepend(str(WATCHER))
    import events
    assert [e["type"] for e in events.read(str(pathlib.Path(home) / "events.jsonl"))] == [
        "request_seen", "review_started"]


# --- single config block -----------------------------------------------------


def test_config_show_exposes_limits_and_paths():
    cfg = json.loads(subprocess.run(
        [sys.executable, str(CONFIG), "show"], capture_output=True, text=True, check=True
    ).stdout)
    assert cfg["CHECK_INTERVAL_MINUTES"] == 10
    assert cfg["MAX_CONCURRENT_REVIEWS"] == 2
    assert cfg["REVIEW_TIMEOUT_MINUTES"] == 45
    assert cfg["ACTIVE_WINDOW_START"] == "08:50" and cfg["ACTIVE_WINDOW_END"] == "18:00"
    assert cfg["TIMEZONE"] == "America/Los_Angeles"
    assert os.path.isabs(cfg["CLAUDE_BIN"]) and cfg["CLAUDE_BIN"].endswith("/.local/bin/claude")
    assert os.path.isabs(cfg["CLAUDE_CONFIG_DIR"])
    assert cfg["EVENT_LOG_PATH"].endswith("events.jsonl")


def test_no_constants_duplicated_outside_config():
    for path in WATCHER.glob("*.py"):
        if path.name == "config.py":
            continue
        text = path.read_text()
        for literal in ("America/Los_Angeles", "events.jsonl", "state.json",
                        "fnm/node-versions", "time(9", "time(18", "time(8"):
            assert literal not in text, f"{path.name} restates {literal!r}"


def test_event_log_path_follows_config_home(home):
    out = run(home, "log-path").strip()
    assert out == str(pathlib.Path(home) / "events.jsonl")
