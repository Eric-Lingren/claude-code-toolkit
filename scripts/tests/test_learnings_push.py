"""Tests for learnings-lane/learnings_push.py. Temp git repos + a bare 'remote' only."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "learnings-lane"))
import learnings_push as lp  # noqa: E402

REL = lp.LEARNINGS_REL


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          check=True).stdout.strip()


def clone(bare, dest):
    git(dest.parent, "clone", "-q", str(bare), str(dest))
    git(dest, "config", "user.email", "t@example.com")
    git(dest, "config", "user.name", "T")
    git(dest, "checkout", "-q", "-B", "main")
    return dest


@pytest.fixture
def env(tmp_path):
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    git(seed, "config", "user.email", "t@example.com")
    git(seed, "config", "user.name", "T")
    (seed / REL).parent.mkdir(parents=True)
    (seed / REL).write_text('{"id":1}\n')
    (seed / "other.txt").write_text("x\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "-q", "origin", "main")
    work = clone(bare, tmp_path / "work")
    return {"bare": bare, "work": work, "tmp": tmp_path}


def test_staged_foreign_file_refuses_without_commit(env):
    work = env["work"]
    (work / REL).write_text('{"id":1}\n{"id":2}\n')
    (work / "other.txt").write_text("changed\n")
    git(work, "add", "other.txt")
    before = git(work, "rev-parse", "HEAD")
    assert lp.main(["--repo", str(work)]) == lp.EXIT_REFUSED
    assert git(work, "rev-parse", "HEAD") == before
    assert git(env["bare"], "rev-parse", "main") == before


def test_staged_new_file_refuses(env):
    work = env["work"]
    (work / "new.txt").write_text("n\n")
    git(work, "add", "new.txt")
    result = lp.push_learnings(work)
    assert result["status"] == "refused"
    assert "new.txt" in result["message"]


def test_unpushed_foreign_commit_refuses(env):
    work = env["work"]
    (work / "other.txt").write_text("local work\n")
    git(work, "commit", "-q", "-am", "wip")
    (work / REL).write_text('{"id":1}\n{"id":2}\n')
    before = git(work, "rev-parse", "HEAD")
    assert lp.push_learnings(work)["status"] == "refused"
    assert git(work, "rev-parse", "HEAD") == before
    assert git(env["bare"], "rev-parse", "main") != before


def test_not_on_main_refuses(env):
    work = env["work"]
    git(work, "checkout", "-q", "-b", "feature")
    (work / REL).write_text('{"id":1}\n{"id":2}\n')
    assert lp.main(["--repo", str(work)]) == lp.EXIT_REFUSED


def test_no_change_exits_zero_without_commit(env):
    work = env["work"]
    before = git(work, "rev-parse", "HEAD")
    assert lp.main(["--repo", str(work)]) == lp.EXIT_OK
    assert git(work, "rev-parse", "HEAD") == before
    assert lp.push_learnings(work)["status"] == "nothing"


def test_change_is_committed_and_pushed(env):
    work = env["work"]
    (work / REL).write_text('{"id":1}\n{"id":2}\n')
    (work / "other.txt").write_text("unstaged edit stays out\n")
    assert lp.main(["--repo", str(work)]) == lp.EXIT_OK
    assert git(env["bare"], "rev-parse", "main") == git(work, "rev-parse", "HEAD")
    assert git(work, "show", "--name-only", "--format=", "HEAD") == REL
    assert git(work, "status", "--porcelain") == "M other.txt"


def test_rejected_push_fetches_rebases_and_retries(env, monkeypatch):
    work = env["work"]
    # Another machine pushes first, so work's push is rejected as non-fast-forward.
    other = clone(env["bare"], env["tmp"] / "other")
    (other / "elsewhere.txt").write_text("e\n")
    git(other, "add", "elsewhere.txt")
    git(other, "commit", "-q", "-m", "other machine")
    git(other, "push", "-q", "origin", "main")
    other_head = git(other, "rev-parse", "HEAD")

    (work / REL).write_text('{"id":1}\n{"id":2}\n')
    calls = []
    real_git = lp._git

    def spy(repo, *args, timeout=60):
        calls.append(args[0])
        return real_git(repo, *args, timeout=timeout)

    monkeypatch.setattr(lp, "_git", spy)
    result = lp.push_learnings(work)
    assert result["status"] == "pushed", result
    assert calls.count("push") == 2  # rejected once, then retried
    after_first_push = calls[calls.index("push") + 1:]
    assert after_first_push.index("fetch") < after_first_push.index("rebase")
    assert "rebase" in after_first_push and "push" in after_first_push
    assert git(env["bare"], "rev-parse", "main") == git(work, "rev-parse", "HEAD")
    subprocess.run(["git", "merge-base", "--is-ancestor", other_head, "HEAD"], cwd=str(work),
                   check=True)
    assert git(work, "show", "--name-only", "--format=", "HEAD") == REL


def test_rebase_conflict_is_aborted_and_fails(env):
    work = env["work"]
    other = clone(env["bare"], env["tmp"] / "other")
    (other / REL).write_text('{"id":1}\n{"id":"theirs"}\n')
    git(other, "commit", "-q", "-am", "theirs")
    git(other, "push", "-q", "origin", "main")
    (work / REL).write_text('{"id":1}\n{"id":"ours"}\n')
    result = lp.push_learnings(work)
    assert result["status"] == "failed"
    assert git(work, "status", "--porcelain") == ""
    assert (work / REL).read_text() == '{"id":1}\n{"id":"ours"}\n'
    assert git(work, "rev-list", "--count", "origin/main..HEAD") == "1"


def test_concurrent_appends_union_merge_cleanly(env):
    work = env["work"]
    # Ship the repo's real .gitattributes to the remote, then re-sync work.
    attrs = Path(__file__).resolve().parents[3] / ".gitattributes"
    other = clone(env["bare"], env["tmp"] / "other")
    (other / ".gitattributes").write_text(attrs.read_text())
    git(other, "add", ".gitattributes")
    git(other, "commit", "-q", "-m", "attrs")
    git(other, "push", "-q", "origin", "main")
    git(work, "pull", "-q", "--ff-only", "origin", "main")
    (other / REL).write_text('{"id":1}\n{"id":"theirs"}\n')
    git(other, "commit", "-q", "-am", "theirs")
    git(other, "push", "-q", "origin", "main")
    (work / REL).write_text('{"id":1}\n{"id":"ours"}\n')
    result = lp.push_learnings(work)
    assert result["status"] == "pushed", result
    text = (work / REL).read_text()
    assert '"theirs"' in text and '"ours"' in text
