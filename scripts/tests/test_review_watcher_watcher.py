"""Tests for review-watcher watcher.sh (one tick).

gh and claude are stubs; the repo is a throwaway local git repo with a bare
"origin" that carries refs/pull/N/head. Nothing real is called and the real
work clone is never touched.
"""

import json
import os
import pathlib
import signal
import stat
import subprocess
import sys
import time

import pytest

WATCHER_DIR = pathlib.Path(__file__).resolve().parents[1] / "review-watcher"
WATCHER = WATCHER_DIR / "watcher.sh"
EVENTS = WATCHER_DIR / "events.py"
WT_ENGINE = pathlib.Path(__file__).resolve().parents[3] / ".scripts" / "worktree"
ME = "Eric-Lingren"
REPO = "acme/app"
INSIDE = "2026-11-04T10:00:00-08:00"   # Wednesday, in the window
OUTSIDE = "2026-11-07T10:00:00-08:00"  # Saturday

GH = """#!/bin/sh
echo "$*" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "api graphql") cat "$FAKE_GRAPHQL" ;;
  "api user") echo "$FAKE_LOGIN" ;;
  "pr view")
    f="$FAKE_REVIEWS/$3.json"
    if [ -f "$f" ]; then cat "$f"; else echo '{"reviews":[]}'; fi ;;
esac
"""

CLAUDE = """#!/bin/bash
first="$(printf '%s\\n' "$2" | head -1)"
pr="$(printf '%s' "$first" | sed -n 's#^/pr-code-review \\([0-9]*\\) .*#\\1#p')"
run_dir="$(printf '%s' "$first" | sed -n 's#.*--headless \\(.*\\)$#\\1#p')"
pwd > "$FAKE_REL/cwd-$pr"
while [ ! -f "$FAKE_REL/release-$pr" ]; do sleep 0.1; done
echo '[{"file":"a.py","line":1,"body":"nit"}]' > "$run_dir/findings.json"
echo '# Summary' > "$run_dir/summary.md"
echo "{\\"type\\":\\"result\\",\\"session_id\\":\\"sess-$pr\\",\\"permission_denials\\":[]}"
"""


def user(login):
    return {"__typename": "User", "login": login}


def pr_node(number, created):
    return {
        "number": number, "isDraft": False, "headRefOid": "aaa", "url": f"u/{number}",
        "title": f"t{number}", "repository": {"nameWithOwner": REPO},
        "author": user("alice"),
        "reviewRequests": {"nodes": [{"requestedReviewer": user(ME)}]},
        "timelineItems": {"nodes": [
            {"__typename": "ReviewRequestedEvent", "id": f"RRE_{number}",
             "createdAt": created, "requestedReviewer": user(ME)}]},
    }


def executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def sh(*args, cwd=None, env=None):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=True)


class Rig:
    def __init__(self, tmp):
        self.tmp = tmp
        self.home = tmp / "home"
        self.rel = tmp / "rel"
        self.reviews = tmp / "reviews"
        for d in (self.rel, self.reviews):
            d.mkdir()
        self.graphql = tmp / "graphql.json"
        self.gh_log = tmp / "gh.log"
        self.gh_log.write_text("")
        gh = executable(tmp / "gh", GH)
        claude = executable(tmp / "claude", CLAUDE)
        git_env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.origin = tmp / "origin.git"
        self.clone = tmp / "clone"
        sh("git", "init", "--bare", "-q", str(self.origin), env=git_env)
        sh("git", "clone", "-q", str(self.origin), str(self.clone), env=git_env)
        (self.clone / "f.txt").write_text("x\n")
        sh("git", "add", ".", cwd=self.clone, env=git_env)
        sh("git", "commit", "-qm", "init", cwd=self.clone, env=git_env)
        self.git_env = git_env
        self.env = dict(
            os.environ, REVIEW_WATCHER_HOME=str(self.home), REVIEW_WATCHER_GH=str(gh),
            REVIEW_WATCHER_CLAUDE_BIN=str(claude), REVIEW_WATCHER_NOW=INSIDE,
            REVIEW_WATCHER_CMUX="/usr/bin/false", REVIEW_WATCHER_NOTIFY="/usr/bin/true", REVIEW_WATCHER_ALERTER="",
            REVIEW_WATCHER_CHECKOUTS=json.dumps({REPO: str(self.clone)}),
            FAKE_GH_LOG=str(self.gh_log), FAKE_GRAPHQL=str(self.graphql),
            FAKE_LOGIN=ME, FAKE_REVIEWS=str(self.reviews), FAKE_REL=str(self.rel),
            REVIEW_WATCHER_WORKTREE_ENGINE=str(WT_ENGINE), WORKTREE_BASE=str(tmp / "worktrees"),
        )
        self.load()

    def load(self, *prs):
        self.graphql.write_text(json.dumps(
            {"data": {"viewer": {"login": ME}, "search": {"nodes": list(prs)}}}))

    def publish_pr(self, n):
        sh("git", "push", "-q", "origin", f"HEAD:refs/pull/{n}/head",
           cwd=self.clone, env=self.git_env)

    def tick(self, *args, now=None):
        env = dict(self.env)
        if now:
            env["REVIEW_WATCHER_NOW"] = now
        return subprocess.run(["bash", str(WATCHER), *args], capture_output=True,
                              text=True, env=env)

    def release(self, n):
        (self.rel / f"release-{n}").write_text("go")

    def states(self):
        p = subprocess.run([sys.executable, str(EVENTS), "state", "--json"],
                           capture_output=True, text=True, env=self.env, check=True)
        return {e["request_id"]: e for e in json.loads(p.stdout)}

    def events(self):
        path = self.home / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]

    def wait_state(self, rid, state, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if self.states().get(rid, {}).get("state") == state:
                return
            time.sleep(0.2)
        raise AssertionError(f"{rid} never reached {state}: {self.states().get(rid)}")

    def append(self, etype, n, rid, ts=None, data=None):
        args = [sys.executable, str(EVENTS), "append", etype, "--repo", REPO, "--pr", str(n),
                "--request-id", rid]
        if ts:
            args += ["--ts", ts]
        if data:
            args += ["--data", json.dumps(data)]
        subprocess.run(args, env=self.env, check=True, capture_output=True)

    def cleanup(self):
        for path in (self.home / "runs").glob("*/run.json"):
            try:
                os.killpg(json.loads(path.read_text())["pid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError, ValueError):
                pass


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.cleanup()


# --- office-hours gate and dry run -------------------------------------------


def test_outside_window_exits_silently_without_polling(rig):
    rig.load(pr_node(7, "2026-11-04T09:00:00Z"))
    p = rig.tick("--once", "--dry-run", now=OUTSIDE)
    assert p.returncode == 0
    assert p.stdout == "" and p.stderr == ""
    assert rig.gh_log.read_text() == ""


def test_outside_window_also_silent_for_real_tick(rig):
    p = rig.tick("--once", now="2026-11-04T07:00:00-08:00")  # before 8:50
    assert p.returncode == 0 and p.stdout == ""
    assert rig.gh_log.read_text() == ""
    p = rig.tick("--once", now="2026-11-04T18:00:00-08:00")  # window end is exclusive
    assert p.stdout == "" and rig.gh_log.read_text() == ""


def test_manual_window_lets_an_after_hours_tick_run(rig):
    rig.load(pr_node(7, "2026-11-04T09:00:00Z"))
    rig.home.mkdir(exist_ok=True)
    (rig.home / "manual-until").write_text("2026-11-04T21:30:00-08:00")
    p = rig.tick("--once", "--dry-run", now="2026-11-04T20:00:00-08:00")
    assert p.returncode == 0, p.stderr
    assert f"would start {REPO}#7 RRE_7" in p.stdout
    p = rig.tick("--once", "--dry-run", now="2026-11-04T21:30:00-08:00")  # end is exclusive
    assert p.stdout == ""


def test_manual_window_extend_never_shortens(rig):
    # Own process: config reads REVIEW_WATCHER_HOME at import time.
    code = """
import manual_window
from datetime import datetime
t = datetime.fromisoformat("2026-11-04T20:00:00-08:00")
end = manual_window.extend(t)
assert end == datetime.fromisoformat("2026-11-04T21:30:00-08:00"), end
assert manual_window.is_open(t) and not manual_window.is_open(end)
assert manual_window.extend(datetime.fromisoformat("2026-11-04T19:00:00-08:00")) == end
open(manual_window.path(), "w").write("garbage")
assert manual_window.until() is None and not manual_window.is_open(t)
"""
    p = subprocess.run([sys.executable, "-c", code], cwd=WATCHER_DIR, env=rig.env,
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_inside_window_dry_run_lists_what_it_would_start(rig):
    rig.load(pr_node(7, "2026-11-04T09:00:00Z"))
    p = rig.tick("--once", "--dry-run")
    assert p.returncode == 0, p.stderr
    assert f"would start {REPO}#7 RRE_7" in p.stdout
    assert rig.events() == []  # dry run writes nothing
    assert not (rig.home / "runs").exists()


# --- concurrency -------------------------------------------------------------


def test_only_two_of_three_start_and_third_starts_when_one_finishes(rig):
    rig.load(pr_node(1, "2026-11-04T09:00:00Z"), pr_node(2, "2026-11-04T09:01:00Z"),
             pr_node(3, "2026-11-04T09:02:00Z"))
    for n in (1, 2, 3):
        rig.publish_pr(n)
    p = rig.tick("--once")
    assert p.returncode == 0, p.stderr
    started = [l for l in p.stdout.splitlines() if l.startswith("started")]
    assert len(started) == 2
    assert "RRE_1" in started[0] and "RRE_2" in started[1]
    rig.wait_state("RRE_1", "running")
    rig.wait_state("RRE_2", "running")
    assert rig.states()["RRE_3"]["state"] == "queued"

    # Both still alive: a second tick starts nothing.
    p = rig.tick("--once")
    assert not [l for l in p.stdout.splitlines() if l.startswith("started")]
    assert rig.states()["RRE_3"]["state"] == "queued"

    rig.release(1)
    rig.wait_state("RRE_1", "ready")
    p = rig.tick("--once")
    started = [l for l in p.stdout.splitlines() if l.startswith("started")]
    assert len(started) == 1 and "RRE_3" in started[0]
    rig.wait_state("RRE_3", "running")


def test_run_uses_detached_worktree_and_review_ready_records_it(rig):
    rig.load(pr_node(4, "2026-11-04T09:00:00Z"))
    rig.publish_pr(4)
    rig.tick("--once")
    rig.wait_state("RRE_4", "running")
    wt = rig.home / "runs" / "RRE_4" / "wt"
    assert (wt / "f.txt").exists()
    rig.release(4)
    rig.wait_state("RRE_4", "ready")
    cwd = pathlib.Path((rig.rel / "cwd-4").read_text().strip()).resolve()
    assert cwd == wt.resolve()
    ready = [e for e in rig.events() if e["type"] == "review_ready"][0]
    assert pathlib.Path(ready["data"]["cwd"]).resolve() == wt.resolve()
    assert ready["data"]["session_id"] == "sess-4"
    assert pathlib.Path(ready["data"]["run_dir"]).resolve() == (rig.home / "runs" / "RRE_4").resolve()
    # A ready review keeps its worktree for /reviews.
    rig.tick("--once")
    assert wt.is_dir()
    # The user's clone has no checkout change: only a detached extra worktree.
    listing = sh("git", "worktree", "list", cwd=rig.clone).stdout
    assert "RRE_4/wt" in listing and "detached" in listing


def test_request_for_unknown_repo_is_failed_not_crash(rig):
    node = pr_node(5, "2026-11-04T09:00:00Z")
    node["repository"]["nameWithOwner"] = "other/repo"
    rig.load(node)
    p = rig.tick("--once")
    assert p.returncode == 0, p.stderr
    st = rig.states()["RRE_5"]
    assert st["state"] == "failed"
    assert st["failed_reason"] == "no local checkout for other/repo"


def test_rerequest_is_a_new_run(rig):
    rig.load(pr_node(6, "2026-11-04T09:00:00Z"))
    rig.publish_pr(6)
    rig.tick("--once")
    rig.wait_state("RRE_6", "running")
    rig.release(6)
    rig.wait_state("RRE_6", "ready")
    rig.append("posted", 6, "RRE_6")
    node = pr_node(6, "2026-11-04T09:00:00Z")
    node["timelineItems"]["nodes"].append(
        {"__typename": "ReviewRequestedEvent", "id": "RRE_6b",
         "createdAt": "2026-11-04T12:00:00Z", "requestedReviewer": user(ME)})
    rig.load(node)
    rig.tick("--once")
    rig.wait_state("RRE_6b", "ready")
    assert rig.states()["RRE_6"]["state"] == "posted"
    assert not (rig.home / "runs" / "RRE_6" / "wt").exists()  # terminal: worktree removed


# --- timeout -----------------------------------------------------------------


def test_run_past_timeout_is_killed_and_logged_failed(rig):
    rig.load(pr_node(8, "2026-11-04T09:00:00Z"))
    rig.publish_pr(8)
    rig.env["REVIEW_WATCHER_TIMEOUT_SECONDS"] = "1"
    rig.tick("--once")
    rig.wait_state("RRE_8", "running")
    pid = json.loads((rig.home / "runs" / "RRE_8" / "run.json").read_text())["pid"]
    time.sleep(1.5)
    p = rig.tick("--once")
    assert p.returncode == 0, p.stderr
    st = rig.states()["RRE_8"]
    assert st["state"] == "failed" and st["failed_reason"] == "timeout"
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not (rig.home / "runs" / "RRE_8" / "wt").exists()


# --- posted reconcile --------------------------------------------------------


def test_ready_pr_already_reviewed_by_eric_gets_posted(rig):
    rig.append("request_seen", 9, "RRE_9", ts="2026-11-04T09:00:00+00:00")
    rig.append("review_ready", 9, "RRE_9", ts="2026-11-04T09:20:00+00:00")
    (rig.reviews / "9.json").write_text(json.dumps({"reviews": [
        {"author": {"login": ME}, "submittedAt": "2026-11-04T09:45:00Z", "state": "COMMENTED"}]}))
    p = rig.tick("--once")
    assert p.returncode == 0, p.stderr
    assert rig.states()["RRE_9"]["state"] == "posted"
    assert [e["type"] for e in rig.events()][-1] == "posted"
    # Viewer login came from gh, not a hard-coded value.
    assert "api user" in rig.gh_log.read_text()


def test_review_before_request_or_by_someone_else_does_not_post(rig):
    rig.append("request_seen", 10, "RRE_10", ts="2026-11-04T09:00:00+00:00")
    rig.append("review_ready", 10, "RRE_10", ts="2026-11-04T09:20:00+00:00")
    (rig.reviews / "10.json").write_text(json.dumps({"reviews": [
        {"author": {"login": ME}, "submittedAt": "2026-11-03T09:45:00Z"},
        {"author": {"login": "bob"}, "submittedAt": "2026-11-04T09:45:00Z"},
        {"author": {"login": ME}, "submittedAt": None}]}))
    rig.tick("--once")
    assert rig.states()["RRE_10"]["state"] == "ready"


def test_reconcile_dry_run_changes_nothing(rig):
    rig.append("request_seen", 9, "RRE_9", ts="2026-11-04T09:00:00+00:00")
    rig.append("review_ready", 9, "RRE_9", ts="2026-11-04T09:20:00+00:00")
    (rig.reviews / "9.json").write_text(json.dumps({"reviews": [
        {"author": {"login": ME}, "submittedAt": "2026-11-04T09:45:00Z"}]}))
    p = rig.tick("--once", "--dry-run")
    assert "would mark posted" in p.stdout
    assert rig.states()["RRE_9"]["state"] == "ready"


# --- helpers / config --------------------------------------------------------


def test_request_id_is_sanitized_for_paths():
    sys.path.insert(0, str(WATCHER_DIR))
    try:
        import watcher
    finally:
        sys.path.pop(0)
    assert watcher.safe_id("RRE_kwDO/../x y") == "RRE_kwDO_.._x_y"
    assert "/" not in watcher.safe_id("a/b\\c") and not watcher.safe_id("../../etc").startswith(".")


def test_config_exposes_runs_dir_and_checkouts(rig):
    cfg = json.loads(sh(sys.executable, str(WATCHER_DIR / "config.py"), "show",
                        env=dict(os.environ, REVIEW_WATCHER_HOME="/x/h")).stdout)
    assert cfg["RUNS_DIR"] == "/x/h/runs"
    checkouts = cfg["REPO_CHECKOUTS"]
    assert checkouts and all(os.path.isabs(p) for p in checkouts.values())


# --- `wt` branch worktrees ---------------------------------------------------


def head_branch(rig, n, branch, cross=False):
    (rig.reviews / f"{n}.json").write_text(json.dumps(
        {"headRefName": branch, "isCrossRepository": cross, "reviews": []}))


def commit(rig, name):
    (rig.clone / name).write_text(name)
    sh("git", "add", ".", cwd=rig.clone, env=rig.git_env)
    sh("git", "commit", "-qm", name, cwd=rig.clone, env=rig.git_env)
    return sh("git", "rev-parse", "HEAD", cwd=rig.clone).stdout.strip()


def push(rig, *refs):
    sh("git", "push", "-q", "origin", *refs, cwd=rig.clone, env=rig.git_env)


def run_cwd(rig, n):
    rid = f"RRE_{n}"
    rig.wait_state(rid, "running")
    deadline = time.time() + 20
    while time.time() < deadline and not (rig.rel / f"cwd-{n}").exists():
        time.sleep(0.1)
    return pathlib.Path((rig.rel / f"cwd-{n}").read_text().strip()).resolve()


def git_out(path, *args):
    return sh("git", "-C", str(path), *args).stdout.strip()


def test_same_repo_pr_runs_in_wt_branch_worktree(rig, tmp_path):
    sha = commit(rig, "a.txt")
    push(rig, "HEAD:refs/heads/feat/x", "HEAD:refs/pull/5/head")
    head_branch(rig, 5, "feat/x")
    rig.load(pr_node(5, "2026-11-04T09:00:00Z"))
    rig.tick("--once")
    wt = (tmp_path / "worktrees" / "clone" / "feat" / "x").resolve()
    assert run_cwd(rig, 5) == wt
    assert git_out(wt, "symbolic-ref", "--short", "HEAD") == "feat/x"
    assert git_out(wt, "rev-parse", "HEAD") == sha
    assert not (rig.home / "runs" / "RRE_5" / "wt").exists()
    rig.release(5)
    rig.wait_state("RRE_5", "ready")
    rig.append("failed", 5, "RRE_5", data={"reason": "x"})
    rig.tick("--once")
    assert wt.is_dir()  # wt worktrees are the user's to remove


def test_fork_pr_uses_detached_worktree(rig, tmp_path):
    commit(rig, "a.txt")
    push(rig, "HEAD:refs/heads/feat/f", "HEAD:refs/pull/6/head")
    head_branch(rig, 6, "feat/f", cross=True)
    rig.load(pr_node(6, "2026-11-04T09:00:00Z"))
    rig.tick("--once")
    assert run_cwd(rig, 6) == (rig.home / "runs" / "RRE_6" / "wt").resolve()
    assert not (tmp_path / "worktrees").exists()
    rig.release(6)


def test_branch_missing_on_origin_is_never_created_from_main(rig, tmp_path):
    commit(rig, "a.txt")
    push(rig, "HEAD:refs/pull/7/head")
    head_branch(rig, 7, "feat/gone")
    rig.load(pr_node(7, "2026-11-04T09:00:00Z"))
    rig.tick("--once")
    assert run_cwd(rig, 7) == (rig.home / "runs" / "RRE_7" / "wt").resolve()
    assert git_out(rig.clone, "branch", "--list", "feat/gone") == ""
    rig.release(7)


def test_existing_worktree_at_other_commit_is_left_alone(rig, tmp_path):
    old = git_out(rig.clone, "rev-parse", "HEAD")
    sh("git", "branch", "feat/mine", cwd=rig.clone)
    mine = tmp_path / "mine"
    sh("git", "worktree", "add", "-q", str(mine), "feat/mine", cwd=rig.clone)
    commit(rig, "a.txt")
    push(rig, "HEAD:refs/heads/feat/mine", "HEAD:refs/pull/8/head")
    head_branch(rig, 8, "feat/mine")
    rig.load(pr_node(8, "2026-11-04T09:00:00Z"))
    rig.tick("--once")
    assert run_cwd(rig, 8) == (rig.home / "runs" / "RRE_8" / "wt").resolve()
    assert git_out(mine, "rev-parse", "HEAD") == old
    rig.release(8)


def test_new_worktree_of_stale_local_branch_is_fast_forwarded(rig, tmp_path):
    sh("git", "branch", "feat/y", cwd=rig.clone)  # local branch at the old commit
    sha = commit(rig, "b.txt")
    push(rig, "HEAD:refs/heads/feat/y", "HEAD:refs/pull/9/head")
    head_branch(rig, 9, "feat/y")
    rig.load(pr_node(9, "2026-11-04T09:00:00Z"))
    rig.tick("--once")
    wt = (tmp_path / "worktrees" / "clone" / "feat" / "y").resolve()
    assert run_cwd(rig, 9) == wt
    assert git_out(wt, "rev-parse", "HEAD") == sha
    rig.release(9)
