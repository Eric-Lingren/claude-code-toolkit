"""Tests for `write-change-records.py --commit` and its weekly-job hook, against temp git repos."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import tempfile

SHARED = Path(__file__).resolve().parents[2]
WRITER = SHARED / "scripts" / "attribution" / "write-change-records.py"
WRAPPER = SHARED / "scripts" / "attribution" / "commit-change-records.sh"
WEEKLY = SHARED / "scripts" / "usage" / "weekly-usage-report.sh"

# A stub `gh` first on PATH so no test here ever reaches the real GitHub CLI
# (fetch_ci_snapshot shells out to it for every manual-lane merge). Set
# CC_TEST_GH_LOG to record each invocation.
_STUB_BIN = Path(tempfile.mkdtemp(prefix="cc-stub-gh-"))
(_STUB_BIN / "gh").write_text(
    '#!/bin/sh\n[ -n "$CC_TEST_GH_LOG" ] && echo "$@" >> "$CC_TEST_GH_LOG"\nexit 1\n')
(_STUB_BIN / "gh").chmod(0o755)
STUB_PATH = f"{_STUB_BIN}{os.pathsep}{os.environ.get('PATH', '')}"

ENV = dict(
    os.environ,
    PATH=STUB_PATH,
    GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.com",
    GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.com",
    GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
)
# Never run the real drift detector (it could raise a real notification).
NO_DRIFT = Path("/usr/bin/true")
CHANGES = "attribution/change-records"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, env=ENV, check=True,
                          capture_output=True, text=True).stdout.rstrip()


def write(repo, rel, text):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def merge_script_change(repo, name, pr):
    git(repo, "checkout", "-q", "-b", name, "main")
    write(repo, f"claude-code-shared/scripts/{name}.py", f"print('{name}')\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"work on {name}")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--no-ff", "-q", name, "-m", f"Merge pull request #{pr} from someone/{name}")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, "README.md", "readme\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "initial")
    merge_script_change(r, "alpha", 1)
    merge_script_change(r, "beta", 2)
    return r


def run_commit(repo, *extra):
    return subprocess.run(
        [sys.executable, str(WRITER), "--repo", str(repo), "--since", "2000-01-01",
         "--out-dir", str(Path(repo) / CHANGES), "--commit", *extra],
        cwd=repo, env=ENV, capture_output=True, text=True)


def head_files(repo):
    return git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()


def test_commit_creates_one_commit_with_only_record_files(repo):
    before = git(repo, "rev-list", "--count", "HEAD")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert int(git(repo, "rev-list", "--count", "HEAD")) == int(before) + 1
    files = head_files(repo)
    assert len(files) == 2
    assert all(f.startswith(CHANGES + "/chg-") and f.endswith(".json") for f in files)
    assert git(repo, "status", "--porcelain") == ""


def test_failed_commit_is_recovered_by_a_later_run(repo):
    git(repo, "config", "commit.gpgsign", "true")
    git(repo, "config", "gpg.program", "/usr/bin/false")
    assert run_commit(repo).returncode == 1  # records written, commit failed
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "config", "--unset", "commit.gpgsign")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert git(repo, "rev-parse", "HEAD") != head
    assert len(head_files(repo)) == 2
    assert git(repo, "status", "--porcelain") == ""


def test_commit_with_nothing_new_makes_no_commit(repo):
    assert run_commit(repo).returncode == 0
    head = git(repo, "rev-parse", "HEAD")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert git(repo, "rev-parse", "HEAD") == head
    assert "nothing to commit" in proc.stdout


def test_commit_with_no_qualifying_merges_makes_no_commit(tmp_path):
    r = tmp_path / "empty"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, "README.md", "x\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "initial")
    head = git(r, "rev-parse", "HEAD")
    assert run_commit(r).returncode == 0
    assert git(r, "rev-parse", "HEAD") == head


def test_dirty_tree_is_not_swept_into_commit(repo):
    write(repo, "README.md", "modified\n")                  # unstaged edit
    write(repo, "untracked.txt", "new\n")                   # untracked file
    write(repo, "staged.txt", "staged\n")                   # staged new file
    git(repo, "add", "staged.txt")
    write(repo, f"{CHANGES}/not-a-record.txt", "stray\n")   # non-json file in the same dir
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    files = head_files(repo)
    assert files and all(f.startswith(CHANGES + "/chg-") and f.endswith(".json") for f in files)
    status = git(repo, "status", "--porcelain").splitlines()
    assert " M README.md" in status
    assert "?? untracked.txt" in status
    assert "A  staged.txt" in status
    assert f"?? {CHANGES}/not-a-record.txt" in status


def test_pre_commit_hook_does_not_run_or_pull_files_in(repo):
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho hooked > hook-made.txt\ngit add hook-made.txt\n")
    hook.chmod(0o755)
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    files = head_files(repo)
    assert files and all(f.startswith(CHANGES + "/chg-") and f.endswith(".json") for f in files)
    assert not (repo / "hook-made.txt").exists()


def add_origin(repo, tmp_path, push=True):
    """Bare local 'GitHub' for the fixture repo. No network, ever."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    if push:
        git(repo, "push", "-q", "origin", "main")
    return origin


def move_github_main(tmp_path, origin, rel="elsewhere.txt", text="from github\n"):
    """Someone else lands a commit on GitHub's main."""
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], env=ENV, check=True,
                   capture_output=True)
    write(other, rel, text)
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "someone else's work")
    git(other, "push", "-q", "origin", "main")
    return git(other, "rev-parse", "HEAD")


def test_commit_pushes_record_commits(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert git(origin, "rev-parse", "main") == git(repo, "rev-parse", "HEAD")
    assert "pushed 1 record commit to origin/main" in proc.stdout


def test_main_moved_brings_it_in_without_plain_pull_and_pushes(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    git(repo, "config", "pull.ff", "only")  # a plain `git pull` would fail once records exist
    github_tip = move_github_main(tmp_path, origin)
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert "warning" not in proc.stdout
    head = git(repo, "rev-parse", "HEAD")
    assert git(origin, "rev-parse", "main") == head
    # Records sit on top of GitHub's commit, history stays linear, no merge commit.
    assert git(repo, "rev-parse", "HEAD~1") == github_tip
    assert git(repo, "rev-list", "--merges", f"{github_tip}..HEAD") == ""
    assert (repo / "elsewhere.txt").exists()
    assert all(f.startswith(CHANGES + "/chg-") for f in head_files(repo))


def test_plain_pull_would_fail_with_ff_only(repo, tmp_path):
    """Control for the test above: the pull the job must avoid really does fail."""
    assert run_commit(repo).returncode == 0  # no remote yet, so the record commit stays local
    origin = add_origin(repo, tmp_path, push=False)
    git(repo, "push", "-q", "origin", "HEAD~1:refs/heads/main")
    git(repo, "config", "pull.ff", "only")
    move_github_main(tmp_path, origin)
    pull = subprocess.run(["git", "pull", "-q", "origin", "main"], cwd=repo, env=ENV,
                          capture_output=True, text=True)
    assert pull.returncode != 0


def test_dirty_tree_and_moved_main_skips_push_without_touching_tree(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    move_github_main(tmp_path, origin)
    before = git(origin, "rev-parse", "main")
    write(repo, "README.md", "dirty edit\n")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert "warning: push skipped" in proc.stdout
    assert "uncommitted" in proc.stdout
    assert git(origin, "rev-parse", "main") == before
    # No autostash, no rebase: the edit is still there and no stash entry exists.
    assert (repo / "README.md").read_text() == "dirty edit\n"
    assert git(repo, "status", "--porcelain") == " M README.md"
    assert git(repo, "stash", "list") == ""
    assert not (repo / ".git" / "rebase-merge").exists()


def test_dirty_tree_with_main_not_moved_still_pushes(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    write(repo, "README.md", "dirty edit\n")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert git(origin, "rev-parse", "main") == git(repo, "rev-parse", "HEAD")
    assert (repo / "README.md").read_text() == "dirty edit\n"


def test_untracked_files_do_not_block_rebase(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    move_github_main(tmp_path, origin)
    write(repo, "untracked.txt", "new\n")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert git(origin, "rev-parse", "main") == git(repo, "rev-parse", "HEAD")
    assert (repo / "untracked.txt").exists()


def test_head_moved_off_main_before_rebase_is_not_rebased(repo, tmp_path, monkeypatch):
    sys.path.insert(0, str(SHARED / "scripts" / "attribution"))
    import change_records_push as crp
    assert run_commit(repo).returncode == 0
    origin = add_origin(repo, tmp_path, push=False)
    git(repo, "push", "-q", "origin", "HEAD~1:refs/heads/main")
    move_github_main(tmp_path, origin)
    real_git = crp._git

    def switching_git(r, *args, **kw):
        # Simulate the user switching branches between the first check and the rebase.
        if args[:1] == ("merge-base",):
            out = real_git(r, *args, **kw)
            git(r, "checkout", "-q", "-b", "other")
            return out
        return real_git(r, *args, **kw)

    monkeypatch.setattr(crp, "_git", switching_git)
    result = crp.push_change_records(repo, repo / CHANGES)
    assert result["status"] == "skipped"
    assert "not main" in result["message"]
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "other"
    assert git(repo, "rev-parse", "other") == git(repo, "rev-parse", "main")


def test_other_unpushed_work_skips_push_and_warns(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    write(repo, "wip.txt", "unpushed\n")
    git(repo, "add", "wip.txt")
    git(repo, "commit", "-q", "-m", "local wip")
    before = git(origin, "rev-parse", "main")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert git(origin, "rev-parse", "main") == before
    assert "warning: push skipped" in proc.stdout
    assert "not change records" in proc.stdout


def test_other_unpushed_work_and_moved_main_leaves_history_alone(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    write(repo, "wip.txt", "unpushed\n")
    git(repo, "add", "wip.txt")
    git(repo, "commit", "-q", "-m", "local wip")
    move_github_main(tmp_path, origin)
    before = git(origin, "rev-parse", "main")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert git(origin, "rev-parse", "main") == before
    assert "warning: push skipped" in proc.stdout
    # No rebase happened: the other commit is not under local history.
    assert subprocess.run(["git", "merge-base", "--is-ancestor", before, "HEAD"], cwd=repo,
                          env=ENV).returncode != 0


def test_unpushed_merge_commit_counts_as_other_work(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    merge_script_change(repo, "gamma", 3)  # a merge commit nobody pushed
    before = git(origin, "rev-parse", "main")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert git(origin, "rev-parse", "main") == before
    assert "warning: push skipped" in proc.stdout


def test_failed_push_warns_and_exits_zero(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'protected branch' >&2\nexit 1\n")
    hook.chmod(0o755)
    before = git(origin, "rev-parse", "main")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert git(origin, "rev-parse", "main") == before
    assert "warning: push failed" in proc.stdout
    assert "committed" in proc.stdout  # the local record commit still happened


def test_failed_push_is_retried_next_run(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    run_commit(repo)
    hook.unlink()
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert "nothing to commit" in proc.stdout
    assert git(origin, "rev-parse", "main") == git(repo, "rev-parse", "HEAD")


def test_unreachable_remote_warns_and_exits_zero(repo, tmp_path):
    git(repo, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert "warning: push failed" in proc.stdout
    assert git(repo, "status", "--porcelain") == ""


def test_rebase_conflict_is_aborted_and_warns(repo, tmp_path):
    # Local record commit made while no remote exists yet.
    assert run_commit(repo).returncode == 0
    local_head = git(repo, "rev-parse", "HEAD")
    origin = add_origin(repo, tmp_path, push=False)
    git(repo, "push", "-q", "origin", "HEAD~1:refs/heads/main")
    # A conflict that is not an add/add on a record file (a file where the records dir goes).
    move_github_main(tmp_path, origin, rel=CHANGES, text="not a directory\n")
    before = git(origin, "rev-parse", "main")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert "warning: push failed" in proc.stdout
    assert git(origin, "rev-parse", "main") == before
    assert git(repo, "rev-parse", "HEAD") == local_head
    assert not (repo / ".git" / "rebase-merge").exists()
    assert not (repo / ".git" / "rebase-apply").exists()


def test_add_add_conflict_on_record_counts_as_already_recorded(repo, tmp_path):
    # Two machines wrote different bytes for the same merge record; GitHub got there first.
    assert run_commit(repo).returncode == 0
    record = head_files(repo)[0]
    origin = add_origin(repo, tmp_path, push=False)
    git(repo, "push", "-q", "origin", "HEAD~1:refs/heads/main")
    github_tip = move_github_main(tmp_path, origin, rel=record, text='{"ci_snapshot": null}\n')
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert "warning" not in proc.stdout
    # GitHub's bytes win; the local duplicate is dropped.
    assert (repo / record).read_text() == '{"ci_snapshot": null}\n'
    assert git(repo, "stash", "list") == ""
    assert git(repo, "status", "--porcelain") == ""
    assert not (repo / ".git" / "rebase-merge").exists()
    # The other record in the same commit is kept on top of GitHub's commit.
    assert git(origin, "rev-parse", "main") == git(repo, "rev-parse", "HEAD")
    assert git(repo, "rev-parse", "HEAD~1") == github_tip
    kept = head_files(repo)
    assert kept and record not in kept


def test_add_add_conflict_on_every_record_drops_commit_and_stays_converged(repo, tmp_path):
    assert run_commit(repo).returncode == 0
    files = head_files(repo)
    origin = add_origin(repo, tmp_path, push=False)
    git(repo, "push", "-q", "origin", "HEAD~1:refs/heads/main")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], env=ENV, check=True,
                   capture_output=True)
    for f in files:
        write(other, f, '{"from": "github"}\n')
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "records from another machine")
    git(other, "push", "-q", "origin", "main")
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert "warning" not in proc.stdout
    assert git(repo, "rev-list", "origin/main..main") == ""
    for f in files:
        assert (repo / f).read_text() == '{"from": "github"}\n'
    again = run_commit(repo)  # later runs no longer fail the same way
    assert again.returncode == 0, again.stderr
    assert "warning" not in again.stdout


def test_no_remote_is_not_a_warning(repo):
    proc = run_commit(repo)
    assert proc.returncode == 0, proc.stderr
    assert "warning" not in proc.stdout
    assert "no 'origin' remote" in proc.stdout


def test_dry_run_with_commit_makes_no_commit(repo):
    head = git(repo, "rev-parse", "HEAD")
    assert run_commit(repo, "--dry-run").returncode == 0
    assert git(repo, "rev-parse", "HEAD") == head


def test_wrapper_commits_in_configured_repo(repo):
    env = dict(ENV, CC_CHANGE_RECORDS_REPO=str(repo))
    proc = subprocess.run(["bash", str(WRAPPER), "--since", "2000-01-01"], cwd="/",
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert all(f.startswith(CHANGES + "/") for f in head_files(repo))


def run_weekly(tmp_path, writer_body):
    out = tmp_path / "reports"
    stub_writer = tmp_path / "writer.sh"
    stub_writer.write_text("#!/bin/bash\n" + writer_body)
    stub_writer.chmod(0o755)
    stub_bench = tmp_path / "bench.py"
    stub_bench.write_text("print('bench ' + ' '.join(__import__('sys').argv[1:]))\n")
    env = dict(os.environ, PATH=STUB_PATH, CC_USAGE_REPORT_DIR=str(out),
               CC_USAGE_BENCHMARK=str(stub_bench), CC_CHANGE_RECORDS_WRITER=str(stub_writer),
               CC_DRIFT_ALERT=str(NO_DRIFT))
    proc = subprocess.run(["bash", str(WEEKLY)], env=env, capture_output=True, text=True)
    return proc, out


def test_weekly_job_invokes_writer(tmp_path):
    marker = tmp_path / "ran"
    proc, out = run_weekly(tmp_path, f"touch '{marker}'\necho 'committed 2 record(s): abc'\n")
    assert proc.returncode == 0, proc.stderr
    assert marker.exists()
    report = next(out.glob("usage-*.txt")).read_text()
    assert "CHANGE RECORDS" in report
    assert "committed 2 record(s): abc" in report


def test_weekly_job_still_writes_report_if_writer_fails(tmp_path):
    proc, out = run_weekly(tmp_path, "echo boom >&2\nexit 7\n")
    assert proc.returncode == 0, proc.stderr
    report = next(out.glob("usage-*.txt")).read_text()
    assert "bench" in report
    assert "WEEKLY TREND" in report
    assert "change record writer failed" in report
    assert (out / "digest-latest.txt").exists()


def test_weekly_job_survives_missing_writer(tmp_path):
    out = tmp_path / "reports"
    stub_bench = tmp_path / "bench.py"
    stub_bench.write_text("print('bench')\n")
    env = dict(os.environ, PATH=STUB_PATH, CC_USAGE_REPORT_DIR=str(out), CC_USAGE_BENCHMARK=str(stub_bench),
               CC_CHANGE_RECORDS_WRITER=str(tmp_path / "does-not-exist.sh"),
               CC_DRIFT_ALERT=str(NO_DRIFT))
    proc = subprocess.run(["bash", str(WEEKLY)], env=env, capture_output=True, text=True)
    assert proc.returncode == 0
    assert next(out.glob("usage-*.txt")).exists()


def run_wrapper(repo):
    env = dict(ENV, CC_CHANGE_RECORDS_REPO=str(repo))
    return subprocess.run(["bash", str(WRAPPER), "--since", "2000-01-01"], cwd="/",
                          env=env, capture_output=True, text=True)


def test_wrapper_skips_on_feature_branch(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    head = git(repo, "rev-parse", "HEAD")
    proc = run_wrapper(repo)
    assert proc.returncode == 0, proc.stderr
    assert "skipped" in proc.stdout and "feature" in proc.stdout
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == ""


def test_wrapper_skips_on_detached_head(repo):
    git(repo, "checkout", "-q", "--detach")
    head = git(repo, "rev-parse", "HEAD")
    proc = run_wrapper(repo)
    assert proc.returncode == 0, proc.stderr
    assert "skipped" in proc.stdout and "detached HEAD" in proc.stdout
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == ""


def install_failing_pre_commit_hook(repo):
    """Make `git commit` fail even with --no-verify: signing is required but the signer exits 1."""
    git(repo, "config", "commit.gpgsign", "true")
    git(repo, "config", "gpg.program", "false")


def test_commit_failure_unstages_record_files_and_exits_nonzero(repo):
    install_failing_pre_commit_hook(repo)
    head = git(repo, "rev-parse", "HEAD")
    proc = run_commit(repo)
    assert proc.returncode != 0
    assert "commit failed" in proc.stderr
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "diff", "--cached", "--name-only") == ""
    # Records are still on disk, just untracked again.
    status = git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
    assert status and all(line.startswith("?? " + CHANGES + "/chg-") for line in status)


def test_commit_change_records_raises_runtime_error_and_leaves_other_staged_alone(repo):
    sys.path.insert(0, str(WRITER.parent))
    from change_records_commit import commit_change_records, pending_record_files
    assert run_commit(repo, "--dry-run").returncode == 0
    out_dir = repo / CHANGES
    write(repo, f"{CHANGES}/chg-manual.json", "{}\n")
    write(repo, "staged.txt", "staged\n")
    git(repo, "add", "staged.txt")
    install_failing_pre_commit_hook(repo)
    with pytest.raises(RuntimeError, match="git commit failed"):
        commit_change_records(repo, out_dir, ["chg-manual"])
    assert git(repo, "diff", "--cached", "--name-only") == "staged.txt"
    assert f"{CHANGES}/chg-manual.json" in pending_record_files(repo, out_dir)


def run_weekly_real_wrapper(tmp_path, repo):
    """The weekly job with the real wrapper pointed at a fixture repo (benchmark and drift stubbed)."""
    out = tmp_path / "reports"
    stub_bench = tmp_path / "bench.py"
    stub_bench.write_text("print('bench')\n")
    env = dict(ENV, CC_USAGE_REPORT_DIR=str(out), CC_USAGE_BENCHMARK=str(stub_bench),
               CC_CHANGE_RECORDS_WRITER=str(WRAPPER), CC_CHANGE_RECORDS_REPO=str(repo),
               CC_DRIFT_ALERT=str(NO_DRIFT))
    proc = subprocess.run(["bash", str(WEEKLY)], env=env, capture_output=True, text=True)
    return proc, next(out.glob("usage-*.txt")).read_text()


def change_records_section(report):
    return report.split("CHANGE RECORDS")[1].split("DRIFT ALERTS")[0]


def test_weekly_job_pushes_records_when_main_moved(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    git(repo, "config", "pull.ff", "only")
    move_github_main(tmp_path, origin)
    proc, report = run_weekly_real_wrapper(tmp_path, repo)
    assert proc.returncode == 0, proc.stderr
    assert "pushed 1 record commit to origin/main" in report
    assert "warning" not in report
    assert git(origin, "rev-parse", "main") == git(repo, "rev-parse", "HEAD")


def test_weekly_report_warns_when_other_work_is_unpushed(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    write(repo, "wip.txt", "unpushed\n")
    git(repo, "add", "wip.txt")
    git(repo, "commit", "-q", "-m", "local wip")
    before = git(origin, "rev-parse", "main")
    proc, report = run_weekly_real_wrapper(tmp_path, repo)
    assert proc.returncode == 0, proc.stderr
    assert git(origin, "rev-parse", "main") == before
    assert "warning: push skipped" in change_records_section(report)


def test_weekly_job_survives_failed_push_and_reports_it(repo, tmp_path):
    origin = add_origin(repo, tmp_path)
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'protected branch' >&2\nexit 1\n")
    hook.chmod(0o755)
    proc, report = run_weekly_real_wrapper(tmp_path, repo)
    assert proc.returncode == 0, proc.stderr
    section = change_records_section(report)
    assert "warning: push failed" in section
    assert "change record writer failed" not in section
    assert "WEEKLY TREND" in report


def test_stray_json_not_committed_and_invalid_record_blocks_push(repo, tmp_path):
    pytest.importorskip("jsonschema")
    sys.path.insert(0, str(WRITER.parent))
    from change_records_commit import commit_change_records
    from change_records_push import push_change_records
    out_dir = repo / CHANGES
    add_origin(repo, tmp_path)
    write(repo, f"{CHANGES}/chg-stray.json", "{}\n")
    write(repo, f"{CHANGES}/chg-mine.json", "{}\n")
    done = commit_change_records(repo, out_dir, ["chg-mine"])
    assert done["files"] == [f"{CHANGES}/chg-mine.json"]
    assert f"?? {CHANGES}/chg-stray.json" in git(repo, "status", "--porcelain").splitlines()
    res = push_change_records(repo, out_dir)
    assert res["status"] == "skipped"
    assert "fail the change-record schema" in res["message"]
    assert "chg-mine.json" in res["message"]
