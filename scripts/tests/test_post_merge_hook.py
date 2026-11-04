"""Tests for .githooks/post-merge: settings sync, linked-worktree skip, and the background change-record job."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / ".githooks" / "post-merge"
ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
    GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com",
    GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
)


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env=ENV, check=True,
                          capture_output=True, text=True).stdout.rstrip()


def make_repo(tmp_path, job_body):
    """Temp repo with the real hook, a stub settings sync, and a stub record job."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    hooks = repo / ".githooks"
    hooks.mkdir()
    shutil.copy(HOOK, hooks / "post-merge")
    (hooks / "post-merge").chmod(0o755)
    prof = repo / "claude-code-shared" / "scripts" / "profile"
    prof.mkdir(parents=True)
    (prof / "sync-settings.py").write_text("")
    attribution = repo / "claude-code-shared" / "scripts" / "attribution"
    attribution.mkdir(parents=True)
    job = attribution / "commit-change-records.sh"
    job.write_text("#!/bin/bash\n" + job_body)
    job.chmod(0o755)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def run_hook(repo, timeout=15):
    start = time.monotonic()
    r = subprocess.run(["bash", str(repo / ".githooks" / "post-merge")], cwd=repo, env=ENV,
                       capture_output=True, text=True, timeout=timeout)
    return r, time.monotonic() - start


def wait_for(path, secs=5):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


def assert_job_not_started(repo, marker):
    """Prove a negative: run the hook on main in the same repo as a positive control, poll until its
    job finishes, then check the marker holds exactly one run (the control's). A job the earlier
    hook started late would have queued first, run under the same lock, and left a second line."""
    log = repo / ".git" / "change-records-job.log"
    assert not log.exists()  # the hook under test queued nothing
    git(repo, "checkout", "-q", "main")
    r, _ = run_hook(repo)
    assert r.returncode == 0
    assert wait_until(lambda: log.exists() and "finished" in log.read_text())
    assert marker.read_text().split() == ["run"]


def test_main_starts_job_in_background(tmp_path):
    marker = tmp_path / "ran"
    repo = make_repo(tmp_path, f'echo "$CC_CHANGE_RECORDS_REPO" > {marker}\n')
    r, _ = run_hook(repo)
    assert r.returncode == 0
    assert wait_for(marker)
    assert Path(marker.read_text().strip()).resolve() == repo.resolve()


def test_non_main_does_not_start_job(tmp_path):
    marker = tmp_path / "ran"
    repo = make_repo(tmp_path, f"echo run >> {marker}\n")
    git(repo, "checkout", "-q", "-b", "feature")
    r, _ = run_hook(repo)
    assert r.returncode == 0
    assert_job_not_started(repo, marker)


def test_detached_head_does_not_start_job(tmp_path):
    marker = tmp_path / "ran"
    repo = make_repo(tmp_path, f"echo run >> {marker}\n")
    git(repo, "checkout", "-q", "--detach")
    r, _ = run_hook(repo)
    assert r.returncode == 0
    assert_job_not_started(repo, marker)


def test_failing_job_still_exits_zero(tmp_path):
    repo = make_repo(tmp_path, "exit 7\n")
    r, _ = run_hook(repo)
    assert r.returncode == 0


def test_slow_job_does_not_delay_hook(tmp_path):
    repo = make_repo(tmp_path, "sleep 8\n")
    r, elapsed = run_hook(repo)
    assert r.returncode == 0
    assert elapsed < 4


def test_slow_job_does_not_hold_stdout_pipe(tmp_path):
    # git captures hook output; a job inheriting the pipe would block the pull.
    repo = make_repo(tmp_path, "sleep 8\n")
    start = time.monotonic()
    subprocess.run(f'bash {repo}/.githooks/post-merge | cat', shell=True, cwd=repo, env=ENV,
                   capture_output=True, timeout=15)
    assert time.monotonic() - start < 4


def wait_until(fn, secs=10):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def test_job_output_goes_to_log_file(tmp_path):
    repo = make_repo(tmp_path, 'echo job-said-hello\necho job-oops >&2\n')
    r, _ = run_hook(repo)
    assert r.returncode == 0
    log = repo / ".git" / "change-records-job.log"
    assert wait_until(lambda: log.exists() and "job-oops" in log.read_text())
    assert "job-said-hello" in log.read_text()
    assert "job-said-hello" not in r.stdout + r.stderr


def test_concurrent_pulls_serialize_the_job(tmp_path):
    trace = tmp_path / "trace"
    repo = make_repo(tmp_path, f'echo start >> {trace}\nsleep 1\necho end >> {trace}\n')
    run_hook(repo)
    run_hook(repo)
    assert wait_until(lambda: trace.exists() and len(trace.read_text().split()) == 4, secs=15)
    assert trace.read_text().split() == ["start", "end", "start", "end"]


def test_lock_released_after_job(tmp_path):
    repo = make_repo(tmp_path, "true\n")
    run_hook(repo)
    log = repo / ".git" / "change-records-job.log"
    assert wait_until(lambda: log.exists() and "finished" in log.read_text())
    assert wait_until(lambda: not (repo / ".git" / "change-records-job.lock").exists())


def test_stale_lock_from_dead_pid_is_reclaimed(tmp_path):
    marker = tmp_path / "ran"
    repo = make_repo(tmp_path, f"touch {marker}\n")
    lock = repo / ".git" / "change-records-job.lock"
    lock.mkdir()
    dead = subprocess.Popen(["true"])
    dead.wait()
    (lock / "pid").write_text(str(dead.pid))
    run_hook(repo)
    assert wait_for(marker)


def test_exit_trap_leaves_lock_owned_by_another_pid(tmp_path):
    # The job rewrites the lock's pid to a live foreign pid (as if another job reclaimed it);
    # this job's exit must not delete that lock.
    repo = make_repo(tmp_path, 'echo $$ > "$CC_CHANGE_RECORDS_REPO/.git/change-records-job.lock/pid"\n')
    holder = subprocess.Popen(["sleep", "30"])
    try:
        job = repo / "claude-code-shared" / "scripts" / "attribution" / "commit-change-records.sh"
        job.write_text(f'#!/bin/bash\necho {holder.pid} > "$CC_CHANGE_RECORDS_REPO/.git/change-records-job.lock/pid"\n')
        run_hook(repo)
        log = repo / ".git" / "change-records-job.log"
        assert wait_until(lambda: log.exists() and "finished" in log.read_text())
        time.sleep(0.5)
        lock = repo / ".git" / "change-records-job.lock"
        assert lock.exists() and (lock / "pid").read_text().strip() == str(holder.pid)
    finally:
        holder.kill()


def test_live_lock_makes_job_wait_then_give_up(tmp_path):
    marker = tmp_path / "ran"
    repo = make_repo(tmp_path, f"touch {marker}\n")
    lock = repo / ".git" / "change-records-job.lock"
    lock.mkdir()
    holder = subprocess.Popen(["sleep", "30"])
    try:
        (lock / "pid").write_text(str(holder.pid))
        env = dict(ENV, CC_CHANGE_RECORDS_LOCK_WAIT="1")
        subprocess.run(["bash", str(repo / ".githooks" / "post-merge")], cwd=repo, env=env,
                       capture_output=True, timeout=15)
        log = repo / ".git" / "change-records-job.log"
        assert wait_until(lambda: log.exists() and "lock" in log.read_text() and "skipped" in log.read_text())
        assert not marker.exists()
        assert lock.exists()  # a live holder's lock is never stolen
    finally:
        holder.kill()


def test_index_lock_present_skips_cleanly(tmp_path):
    marker = tmp_path / "ran"
    repo = make_repo(tmp_path, f"touch {marker}\n")
    (repo / ".git" / "index.lock").write_text("")
    env = dict(ENV, CC_CHANGE_RECORDS_INDEX_WAIT="1")
    r = subprocess.run(["bash", str(repo / ".githooks" / "post-merge")], cwd=repo, env=env,
                       capture_output=True, text=True, timeout=15)
    assert r.returncode == 0
    log = repo / ".git" / "change-records-job.log"
    assert wait_until(lambda: log.exists() and "index.lock" in log.read_text())
    assert not marker.exists()
    assert wait_until(lambda: not (repo / ".git" / "change-records-job.lock").exists())


def test_linked_worktree_skips_everything(tmp_path):
    marker = tmp_path / "ran"
    repo = make_repo(tmp_path, f"echo run >> {marker}\n")
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "other", str(wt))
    r, _ = run_hook(wt)
    assert r.returncode == 0
    assert_job_not_started(repo, marker)


def test_job_path_points_at_an_executable_file_in_the_repo():
    """A missed rename would make the hook's `[ -x "$JOB" ]` guard skip record-writing silently."""
    import re
    m = re.search(r'^\s*JOB="\$REPO/([^"]+)"', HOOK.read_text(), re.M)
    assert m, "no JOB= line found in post-merge"
    job = ROOT / m.group(1)
    assert job.is_file(), job
    assert os.access(job, os.X_OK), job
