#!/usr/bin/env python3
"""learnings_push.py — commit and push the learnings file straight to GitHub's main, safely.

What: after a kick pass has changed claude-code-shared/learnings/unified-learnings.jsonl, this
  commits that one file and pushes it to origin/main. It deliberately skips the PR gate, the same
  way change records do (see attribution/change_records_push.py, whose shape this copies).
Refusal: it never commits or pushes anything but that one file. Any other STAGED file makes it
  refuse before committing (exit 2). Any unpushed commit on local main that touches more than the
  learnings file also makes it refuse (exit 2), so someone's half-finished work is never pushed. A
  not-on-main checkout is a refusal too. Unstaged edits to other files are left alone: the commit
  is a pathspec (--only) commit.
Retry: a rejected push triggers `git fetch`, `git rebase origin/main`, then another push, up to
  MAX_PUSH_ATTEMPTS pushes in total. It never autostashes: if tracked files are dirty and a
  rebase is needed, it fails with a message instead. The repo's .gitattributes marks the learnings
  file merge=union so concurrent end-of-file appends rebase cleanly. A conflict that still happens
  is aborted, leaving local main as it was; the commit stays unpushed and, because the same
  conflict recurs, later runs fail the same way until a human resolves it.
Nothing to do: with no change to the learnings file and nothing unpushed it exits 0 and makes no
  commit.
Exit codes: 0 pushed or nothing to push; 1 failed (fetch, rebase or push problem); 2 refused.
When: run after a kick pass of the learnings lane; also usable by hand.

Importable: push_learnings.
Usage: learnings_push.py [--repo PATH]
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

import importlib.util

_cspec = importlib.util.spec_from_file_location("lane_config", Path(__file__).resolve().parent / "config.py")
config = importlib.util.module_from_spec(_cspec)
_cspec.loader.exec_module(config)
MAX_PUSH_ATTEMPTS = config.MAX_PUSH_ATTEMPTS
SHORT_SHA_LEN = config.SHORT_SHA_LEN
MAX_LISTED = config.MAX_LISTED
NET_TIMEOUT = config.PUSH_NET_TIMEOUT_SECONDS

REMOTE = "origin"
BRANCH = "main"
REMOTE_REF = f"refs/remotes/{REMOTE}/{BRANCH}"
LEARNINGS_REL = "claude-code-shared/learnings/unified-learnings.jsonl"
COMMIT_SUBJECT = "Update unified learnings"

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2


def _git(repo, *args, timeout=60):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                              check=False, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"git {args[0]} timed out")


def _oneline(proc):
    text = (proc.stderr or proc.stdout or "").strip()
    return " ".join(text.split()) or f"exit {proc.returncode}"


def _result(status, message):
    return {"status": status, "message": message}


def _current_branch(repo):
    return _git(repo, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()


def _lines(proc):
    return [ln for ln in proc.stdout.split("\n") if ln]


def _fetch(repo):
    return _git(repo, "fetch", "-q", REMOTE, f"+refs/heads/{BRANCH}:{REMOTE_REF}",
                timeout=NET_TIMEOUT)


def _only_learnings_commit(repo, sha, rel):
    """True when the commit is a non-merge commit touching nothing but the learnings file."""
    parents = _git(repo, "rev-list", "--parents", "-n", "1", sha).stdout.split()
    if len(parents) != 2:  # merge commits (3+) and root commits (1) are not learnings commits
        return False
    files = _lines(_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha))
    return bool(files) and all(f == rel for f in files)


def _rebase_onto_remote(repo):
    """Fetch and replay local commits onto GitHub's main. Returns None on success, else a result."""
    fetch = _fetch(repo)
    if fetch.returncode != 0:
        return _result("failed", f"warning: push failed: fetch: {_oneline(fetch)}")
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        return _result("failed", (f"warning: push failed: GitHub's {BRANCH} has moved and the "
                                  f"tree has uncommitted changes; commit or stash them, the "
                                  f"next run will push"))
    rebase = _git(repo, "rebase", REMOTE_REF, timeout=NET_TIMEOUT)
    if rebase.returncode != 0:
        _git(repo, "rebase", "--abort")
        return _result("failed", f"warning: push failed: rebase onto {REMOTE}/{BRANCH}: "
                                 f"{_oneline(rebase)}")
    return None


def push_learnings(repo, rel=LEARNINGS_REL):
    """Commit the learnings file if changed, then push it, retrying a rejected push.

    Returns {"status": "pushed"|"nothing"|"refused"|"failed", "message": str}.
    """
    repo = Path(repo).resolve()
    try:
        return _push(repo, rel)
    except Exception as exc:  # an unattended job must never die with a traceback
        return _result("failed", f"warning: push failed: {exc}")


def _push(repo, rel):
    branch = _current_branch(repo)
    if branch != BRANCH:
        return _result("refused", f"refused: on '{branch or 'detached HEAD'}', not {BRANCH}")
    if _git(repo, "remote", "get-url", REMOTE).returncode != 0:
        return _result("failed", f"warning: push failed: no '{REMOTE}' remote")

    staged = _lines(_git(repo, "diff", "--cached", "--name-only"))
    foreign = [f for f in staged if f != rel]
    if foreign:
        shown = ", ".join(foreign[:MAX_LISTED])
        return _result("refused", f"refused: staged changes outside {rel} ({shown}); "
                                  f"nothing committed")

    fetch = _fetch(repo)
    if fetch.returncode != 0:
        return _result("failed", f"warning: push failed: fetch: {_oneline(fetch)}")

    unpushed = _git(repo, "rev-list", f"{REMOTE_REF}..{BRANCH}").stdout.split()
    others = [s for s in unpushed if not _only_learnings_commit(repo, s, rel)]
    if others:
        shown = ", ".join(sha[:SHORT_SHA_LEN] for sha in others[:MAX_LISTED])
        return _result("refused", f"refused: local {BRANCH} has {len(others)} unpushed "
                                  f"commit(s) that touch more than {rel} ({shown}); "
                                  f"push them by hand")

    changed = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", rel)
    if changed.stdout.strip():
        add = _git(repo, "add", "--", rel)
        if add.returncode != 0:
            return _result("failed", f"warning: git add failed: {_oneline(add)}")
        # Trailing path makes this an --only commit. --no-verify: a pre-commit hook that
        # git-adds other files must not pull them into this commit.
        commit = _git(repo, "commit", "-q", "--no-verify", "-m", COMMIT_SUBJECT, "--", rel)
        if commit.returncode != 0:
            _git(repo, "reset", "-q", "--", rel)
            return _result("failed", f"warning: git commit failed: {_oneline(commit)}")
        unpushed = _git(repo, "rev-list", f"{REMOTE_REF}..{BRANCH}").stdout.split()

    if not unpushed:
        return _result("nothing", "nothing to push")

    last = ""
    for attempt in range(1, MAX_PUSH_ATTEMPTS + 1):
        push = _git(repo, "push", "-q", REMOTE, f"refs/heads/{BRANCH}:refs/heads/{BRANCH}",
                    timeout=NET_TIMEOUT)
        if push.returncode == 0:
            n = len(unpushed)
            return _result("pushed", f"pushed {n} learnings commit{'' if n == 1 else 's'} to "
                                     f"{REMOTE}/{BRANCH}")
        last = _oneline(push)
        if attempt == MAX_PUSH_ATTEMPTS:
            break
        problem = _rebase_onto_remote(repo)
        if problem:
            return problem
    return _result("failed", f"warning: push failed after {MAX_PUSH_ATTEMPTS} attempts: {last}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Commit and push unified-learnings.jsonl to main.")
    parser.add_argument("--repo", default=None, help="repo root (default: git toplevel of cwd)")
    args = parser.parse_args(argv)
    repo = args.repo
    if repo is None:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, check=False)
        if top.returncode != 0:
            print("learnings_push: not inside a git repo", file=sys.stderr)
            return EXIT_FAILED
        repo = top.stdout.strip()
    result = push_learnings(repo)
    print(result["message"])
    return {"pushed": EXIT_OK, "nothing": EXIT_OK, "refused": EXIT_REFUSED}.get(
        result["status"], EXIT_FAILED)


if __name__ == "__main__":
    sys.exit(main())
