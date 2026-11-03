"""Tests for the pinned Review Watcher workspace and live queue view.

cmux is a fake python binary keeping a workspace list in a JSON file, so close,
recreate and reorder can be asserted. gh, terminal-notifier and claude are stubs.
The real cmux is never touched.
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
QUEUE_VIEW = WATCHER_DIR / "queue-view.sh"
REPO = "acme/app"
SEEN = "2026-11-04T09:00:00-08:00"
TICK = "2026-11-04T09:40:00-08:00"

GH = """#!/bin/sh
case "$1 $2" in
  "api graphql") echo '{"data":{"viewer":{"login":"me"},"search":{"nodes":[]}}}' ;;
  "api user") echo me ;;
  "pr view") echo '{"reviews":[]}' ;;
esac
"""

FAKE_CMUX = r'''#!/usr/bin/env python3
import json, os, sys
db = os.environ["FAKE_CMUX_DB"]
a = sys.argv[1:]
with open(os.environ["FAKE_CMUX_LOG"], "a") as f:
    f.write(" ".join(a) + "\n")
if os.environ.get("FAKE_CMUX_MODE") == "fail":
    sys.exit(1)
st = json.load(open(db))
def save():
    json.dump(st, open(db, "w"))
def opt(name):
    return a[a.index(name) + 1] if name in a else None
cmd = a[0] if a else ""
if cmd == "list-workspaces":
    for w in st["ws"]:
        print(("* " if w["sel"] else "  ") + w["ref"] + "  " + w["title"])
elif cmd == "new-workspace":
    st["n"] += 1
    ref = "workspace:%d" % st["n"]
    st["ws"].append({"ref": ref, "title": "untitled", "sel": False, "pinned": False,
                     "command": opt("--command")})
    save(); print("OK " + ref)
elif cmd == "rename-workspace":
    for w in st["ws"]:
        if w["ref"] == opt("--workspace"):
            w["title"] = a[-1]
    save()
elif cmd == "reorder-workspace":
    w = next(w for w in st["ws"] if w["ref"] == opt("--workspace"))
    st["ws"].remove(w); st["ws"].insert(int(opt("--index")), w); save()
elif cmd == "workspace-action":
    for w in st["ws"]:
        if w["ref"] == opt("--workspace") and opt("--action") == "pin":
            w["pinned"] = True
    save()
elif cmd == "close-workspace":
    st["ws"] = [w for w in st["ws"] if w["ref"] != opt("--workspace")]; save()
'''


def executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class Rig:
    def __init__(self, tmp):
        self.db = tmp / "cmux.json"
        self.log = tmp / "cmux.log"
        self.log.write_text("")
        self.db.write_text(json.dumps({"n": 3, "ws": [
            {"ref": "workspace:1", "title": "one", "sel": True, "pinned": False},
            {"ref": "workspace:2", "title": "two", "sel": False, "pinned": False}]}))
        self.env = dict(
            os.environ, REVIEW_WATCHER_HOME=str(tmp / "home"),
            REVIEW_WATCHER_GH=str(executable(tmp / "gh", GH)),
            REVIEW_WATCHER_CMUX=str(executable(tmp / "cmux", FAKE_CMUX)),
            REVIEW_WATCHER_NOTIFY=str(executable(tmp / "notify", "#!/bin/sh\nexit 0\n")),
            REVIEW_WATCHER_ALERTER="",
            REVIEW_WATCHER_CHECKOUTS="{}", REVIEW_WATCHER_CLAUDE_BIN="/bin/false",
            FAKE_CMUX_DB=str(self.db), FAKE_CMUX_LOG=str(self.log),
        )

    def workspaces(self):
        return json.loads(self.db.read_text())["ws"]

    def titles(self):
        return [w["title"] for w in self.workspaces()]

    def calls(self):
        return [l for l in self.log.read_text().splitlines() if l.strip()]

    def event(self, etype, n, rid, ts=SEEN, data=None):
        args = [sys.executable, str(EVENTS), "append", etype, "--repo", REPO, "--pr", str(n),
                "--request-id", rid, "--ts", ts]
        if data:
            args += ["--data", json.dumps(data)]
        subprocess.run(args, env=self.env, capture_output=True, text=True, check=True)

    def tick(self, **extra):
        env = dict(self.env, REVIEW_WATCHER_NOW=TICK, **extra)
        return subprocess.run(["bash", str(WATCHER)], capture_output=True, text=True,
                              env=env, timeout=60)

    def view_once(self, **extra):
        return subprocess.run(["bash", str(QUEUE_VIEW), "--once"], capture_output=True,
                              text=True, env=dict(self.env, **extra), timeout=30)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_tick_creates_reviews_workspace_at_top_then_recreates_after_close(rig):
    assert rig.tick().returncode == 0
    ws = rig.workspaces()
    assert ws[0]["title"] == "Review Watcher"
    assert ws[0]["pinned"] is True
    assert ws[0]["command"].endswith("queue-view.sh")
    assert os.path.isfile(ws[0]["command"])
    # a second tick with it in place changes nothing
    before = rig.workspaces()
    rig.tick()
    assert rig.workspaces() == before
    # close it, tick again: recreated at the top
    subprocess.run([rig.env["REVIEW_WATCHER_CMUX"], "close-workspace", "--workspace", ws[0]["ref"]],
                   env=rig.env, check=True)
    assert "Review Watcher" not in rig.titles()
    rig.tick()
    assert rig.titles() == ["Review Watcher", "one", "two"]
    assert rig.workspaces()[0]["pinned"] is True


def test_tick_moves_displaced_workspace_back_to_top(rig):
    rig.tick()
    db = json.loads(rig.db.read_text())
    db["ws"].append(db["ws"].pop(0))  # Review Watcher to the bottom
    rig.db.write_text(json.dumps(db))
    assert rig.titles()[-1] == "Review Watcher"
    rig.tick()
    assert rig.titles()[0] == "Review Watcher"


def test_unreachable_socket_is_noop_and_tick_succeeds(rig):
    r = rig.tick(FAKE_CMUX_MODE="fail")
    assert r.returncode == 0
    assert rig.titles() == ["one", "two"]
    assert not any(c.startswith("new-workspace") for c in rig.calls())


def test_queue_view_once_lists_entries_oldest_first_with_hint(rig):
    rig.event("request_seen", 12, "r2", "2026-11-04T09:10:00-08:00", {"title": "Second"})
    rig.event("request_seen", 11, "r1", SEEN, {"title": "First"})
    rig.event("review_ready", 11, "r1", "2026-11-04T09:20:00-08:00")
    out = rig.view_once().stdout
    assert out.index("#11") < out.index("#12")
    assert "ready" in out and "queued" in out
    assert "First" in out and "Second" in out
    assert "type review-open below" in out
    assert "Commands here: review-open [PR], review-restore PR, review-rerun PR, review-help." in out


def test_queue_view_drops_review_once_opened(rig):
    rig.event("request_seen", 11, "r1", SEEN, {"title": "First"})
    rig.event("review_ready", 11, "r1", "2026-11-04T09:20:00-08:00")
    assert "#11" in rig.view_once().stdout
    rig.event("approved", 11, "r1", "2026-11-04T09:30:00-08:00")
    assert "#11" not in rig.view_once().stdout


def test_queue_view_says_wait_while_nothing_is_ready(rig):
    rig.event("request_seen", 11, "r1", SEEN, {"title": "First"})
    rig.event("review_started", 11, "r1", "2026-11-04T09:20:00-08:00")
    out = rig.view_once().stdout
    assert "running" in out
    assert "Next: nothing. The review runs headless" in out
    assert "type review-open below" not in out


def test_queue_view_empty_queue_says_nothing_to_do(rig):
    out = rig.view_once().stdout
    assert "(queue empty)" in out
    assert "Next: nothing. The watcher checks for review requests" in out


def test_queue_view_loop_picks_up_new_event_within_30_seconds(rig, tmp_path):
    out_file = tmp_path / "view.out"
    env = dict(rig.env, QUEUE_VIEW_REFRESH_SECONDS="1")
    with open(out_file, "w") as fh:
        proc = subprocess.Popen(["bash", str(QUEUE_VIEW)], env=env, stdout=fh,
                                stderr=subprocess.STDOUT)
    try:
        time.sleep(1.5)
        assert "#77" not in out_file.read_text()
        rig.event("request_seen", 77, "rx", SEEN, {"title": "Brand new"})
        deadline = time.time() + 30
        while time.time() < deadline and "#77" not in out_file.read_text():
            time.sleep(0.5)
        assert "acme/app#77" in out_file.read_text()
    finally:
        proc.kill()
        proc.wait()


def test_default_refresh_interval_is_30_seconds():
    p = subprocess.run([sys.executable, str(WATCHER_DIR / "config.py"), "show",
                        "QUEUE_VIEW_REFRESH_SECONDS"], capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items()
                            if k != "QUEUE_VIEW_REFRESH_SECONDS"})
    assert p.stdout.strip() == "30"


def test_pill_updates_from_queue_view_when_watcher_cannot_reach_socket(rig):
    rig.event("request_seen", 11, "r1", SEEN, {"title": "T"})
    rig.event("review_ready", 11, "r1", "2026-11-04T09:20:00-08:00")
    rig.tick()  # creates the workspace
    rig.log.write_text("")
    # watcher side: socket dead, so no pill is written
    assert rig.tick(FAKE_CMUX_MODE="fail").returncode == 0
    # queue view runs inside cmux: socket reachable, pill updates
    rig.log.write_text("")
    rig.view_once()
    pill = [c for c in rig.calls() if c.startswith("set-status reviews 1 ready")]
    assert pill and "--workspace workspace:" in pill[0]


def test_queue_view_moves_displaced_workspace_back_to_top(rig):
    # launchd cannot reach the cmux socket, so the view (inside cmux) keeps the pin
    rig.tick()
    db = json.loads(rig.db.read_text())
    db["ws"].append(db["ws"].pop(0))  # Review Watcher to the bottom
    rig.db.write_text(json.dumps(db))
    rig.view_once()
    assert rig.titles()[0] == "Review Watcher"
    assert rig.workspaces()[0]["pinned"] is True


def test_workspace_ensure_cli_creates_reviews_workspace(rig):
    # .cmux-startup.sh calls this from inside cmux at session start
    r = subprocess.run([sys.executable, str(QUEUE_VIEW.parent / "workspace.py"), "ensure"],
                       capture_output=True, text=True, env=rig.env, timeout=30)
    assert r.returncode == 0
    assert rig.titles()[0] == "Review Watcher"


def test_legacy_reviews_workspace_is_renamed_not_duplicated(rig):
    db = json.loads(rig.db.read_text())
    db["ws"].insert(0, {"ref": "workspace:3", "title": "Reviews", "sel": False, "pinned": True})
    rig.db.write_text(json.dumps(db))
    rig.view_once()
    assert rig.titles() == ["Review Watcher", "one", "two"]
    assert not any(c.startswith("new-workspace") for c in rig.calls())


def run_view_with_input(rig, typed):
    """Loop mode with typed lines on stdin; returns everything it printed."""
    env = dict(rig.env, QUEUE_VIEW_REFRESH_SECONDS="1")
    proc = subprocess.Popen(["bash", str(QUEUE_VIEW)], env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        proc.stdin.write(typed)
        proc.stdin.close()
        proc.stdin = None  # Python < 3.13 communicate() raises ValueError on an already-closed stdin
        time.sleep(4)
    finally:
        proc.kill()
    return proc.communicate()[0]


def test_queue_view_runs_review_help_typed_in_pane(rig):
    out = run_view_with_input(rig, "review-help\n")
    assert "Queue states (Review Watcher pane):" in out


def test_queue_view_refuses_other_commands(rig):
    out = run_view_with_input(rig, "git status\n")
    assert "git status: Only review-open [PR], review-restore PR, review-rerun PR and review-help run here." in out


def test_queue_view_parse_maps_only_review_commands():
    sys.path.insert(0, str(WATCHER_DIR))
    import queue_view
    assert queue_view.parse("review-open") == ["open"]
    assert queue_view.parse("review-open 21358") == ["open", "21358"]
    assert queue_view.parse("review-open #21358") == ["open", "21358"]
    assert queue_view.parse("reviews") is None
    assert queue_view.parse("review-help") == ["help"]
    assert queue_view.parse("review-open 1; ls") is None
    assert queue_view.parse("review-pane") is None
    assert queue_view.parse("review-restore 21342") == ["restore", "21342"]
    assert queue_view.parse("review-rerun #21358") == ["rerun", "21358"]
    assert queue_view.parse("review-rerun") is None  # needs a PR
    assert queue_view.parse("review-restore") is None
    assert queue_view.parse("claude") is None


def test_queue_view_runs_open_request_from_banner(rig, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    rig.event("request_seen", 11, "r1", SEEN, {"title": "First"})
    rig.event("review_ready", 11, "r1", "2026-11-04T09:20:00-08:00",
              {"session_id": "sess-1", "cwd": str(wt), "run_dir": "x"})
    req = pathlib.Path(rig.env["REVIEW_WATCHER_HOME"]) / "open-requests"
    req.mkdir(parents=True, exist_ok=True)
    (req / f"{time.time():.3f}-abc").write_text("11")
    rig.env["CMUX_WORKSPACE_ID"] = "ws-1"
    out = run_view_with_input(rig, "")
    assert "Opened acme/app#11 in its own workspace" in out
    assert not list(req.iterdir())
    assert any(c.startswith("new-workspace --cwd " + str(wt)) for c in rig.calls())
