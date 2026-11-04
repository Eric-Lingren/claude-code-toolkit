"""change_records_push.py — push local change-record commits to GitHub's main, safely.

What: after `write-change-records.py --commit` makes its record commit, this brings in
  GitHub's main and pushes. It only pushes when every unpushed commit on local main is a
  record-only commit (it touches nothing but attribution/change-records/<name>.json). Any other unpushed
  work on main means the push is skipped and a warning is returned, never a push of someone's
  half-finished work.
How it brings in GitHub's main: `git fetch origin main`, then `git rebase origin/main` of the
  record commits on top of it. A plain `git pull` is not usable here: the checkout runs with
  pull.ff=only, which fails as soon as local main holds record commits while GitHub's main has
  moved. Rebasing is safe because the only commits being replayed are record commits (new JSON
  files). It never autostashes: if main has tracked uncommitted edits and a rebase is needed, the
  push is skipped with a warning (a conflicting stash apply would leave the user's tree half
  merged). HEAD is re-checked to still be main right before the rebase. Records are keyed by the
  merged sha only, so another machine may have pushed the same record with different bytes; an
  add/add conflict on a record file is treated as "already recorded" (GitHub's copy wins, the
  local duplicate is dropped). Any other rebase failure is aborted, leaving local main as it was.
Never raises and never reports failure as an error: every problem becomes a "warning:" message
  so the weekly job keeps going (direct pushes to main may be blocked by branch protection; that
  was never confirmed). The next run retries, since the record commits stay unpushed.
When: called by write-change-records.py --commit (weekly via attribution/commit-change-records.sh, and
  by hand). Kept in its own module so write-change-records.py stays small.

Importable: push_change_records.
"""
import os
import subprocess
from pathlib import Path

REMOTE = "origin"
BRANCH = "main"
REMOTE_REF = f"refs/remotes/{REMOTE}/{BRANCH}"
SHORT_SHA_LEN = 10  # sha chars shown in warnings
MAX_LISTED_COMMITS = 5  # commits/records named in a warning
NET_TIMEOUT = 120  # seconds for fetch/push; an unattended job must never hang


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


def _is_record_commit(repo, sha, out_rel):
    """True when the commit is a non-merge commit touching only <out_rel>/<name>.json files."""
    parents = _git(repo, "rev-list", "--parents", "-n", "1", sha).stdout.split()
    if len(parents) != 2:  # merge commits (3+) and root commits (1) are not record commits
        return False
    files = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).stdout.split("\n")
    files = [f for f in files if f]
    return bool(files) and all(
        f.endswith(".json") and Path(f).parent.as_posix() == out_rel for f in files)


def _invalid_records(repo, shas, out_rel):
    """Names of record files in the commits that fail the change-record schema.

    Returns None when jsonschema is unavailable (the caller must then refuse to push).
    """
    try:
        import json
        import jsonschema
    except ImportError:
        return None
    schema_path = Path(__file__).resolve().parents[2] / "contracts" / "change-record-schema.json"
    schema = json.loads(schema_path.read_text())
    bad = []
    for sha in shas:
        names = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).stdout.split()
        for name in names:
            blob = _git(repo, "show", f"{sha}:{name}")
            try:
                jsonschema.validate(json.loads(blob.stdout), schema)
            except Exception:
                bad.append(name)
    return bad


def push_change_records(repo, out_dir):
    """Fetch, rebase onto GitHub's main if it moved, and push the record commits.

    Returns {"status": "pushed"|"nothing"|"skipped"|"failed", "message": str}. Messages for
    "skipped" and "failed" start with "warning:" except when there is simply no remote.
    """
    try:
        return _push(Path(repo).resolve(), out_dir)
    except Exception as exc:  # the weekly job must never die on a push problem
        return {"status": "failed", "message": f"warning: push failed: {exc}"}


def _current_branch(repo):
    return _git(repo, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()


def _add_add_record_conflicts(repo, out_rel):
    """Paths of the rebase's unmerged files when ALL are add/add conflicts on record files.

    Returns [] when there is any other kind of conflict (or none), meaning "not resolvable".
    """
    stages = {}
    for line in _git(repo, "ls-files", "-u").stdout.splitlines():
        meta, _, path = line.partition("\t")
        stages.setdefault(path, set()).add(meta.split()[2])
    if not stages:
        return []
    for path, got in stages.items():
        is_record = path.endswith(".json") and Path(path).parent.as_posix() == out_rel
        if not is_record or got != {"2", "3"}:  # add/add has no stage 1 (no common base)
            return []
    return sorted(stages)


def _resolve_as_already_recorded(repo, out_rel):
    """Mid-rebase: take GitHub's copy of every add/add record conflict, then continue.

    Records are keyed by merged sha only, so the same record can be written on two machines with
    different bytes. GitHub already has it, so the local copy is dropped. A replayed commit left
    with nothing new is skipped. Returns True when the rebase can carry on.
    """
    paths = _add_add_record_conflicts(repo, out_rel)
    if not paths:
        return False
    for path in paths:
        # During a rebase "ours" is the branch being rebased onto, i.e. GitHub's main.
        if _git(repo, "checkout", "--ours", "--", path).returncode != 0:
            return False
        if _git(repo, "add", "--", path).returncode != 0:
            return False
    if _git(repo, "diff", "--cached", "--quiet").returncode == 0:  # nothing left to commit
        step = _git(repo, "rebase", "--skip", timeout=NET_TIMEOUT)
    else:
        step = _git(repo, "-c", "core.editor=true", "rebase", "--continue", timeout=NET_TIMEOUT)
    return step


def _rebase_onto_remote(repo, out_rel):
    """Replay the local record commits onto GitHub's main. Returns None on success, else a result.

    Never autostashes: a tree with tracked edits is left alone and the push is skipped, so a
    conflicting stash apply can never leave the user's files half-merged. HEAD is re-checked just
    before the rebase because the branch check at the top of _push can go stale.
    """
    branch = _current_branch(repo)
    if branch != BRANCH:
        return {"status": "skipped",
                "message": f"push skipped: on '{branch or 'detached HEAD'}', not {BRANCH}"}
    dirty = _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    if dirty:
        return {"status": "skipped",
                "message": (f"warning: push skipped: {BRANCH} has uncommitted changes and GitHub's "
                            f"{BRANCH} has moved; commit or stash them, the next run will push")}
    step = _git(repo, "rebase", REMOTE_REF, timeout=NET_TIMEOUT)
    while step.returncode != 0:
        nxt = _resolve_as_already_recorded(repo, out_rel)
        if nxt is False:
            _git(repo, "rebase", "--abort")
            return {"status": "failed",
                    "message": f"warning: push failed: rebase onto {REMOTE}/{BRANCH}: "
                               f"{_oneline(step)}"}
        step = nxt
    return None


def _push(repo, out_dir):
    branch = _current_branch(repo)
    if branch != BRANCH:
        return {"status": "skipped",
                "message": f"push skipped: on '{branch or 'detached HEAD'}', not {BRANCH}"}
    if _git(repo, "remote", "get-url", REMOTE).returncode != 0:
        return {"status": "skipped", "message": f"push skipped: no '{REMOTE}' remote"}

    out_rel = Path(out_dir).resolve().relative_to(repo).as_posix()
    fetch = _git(repo, "fetch", "-q", REMOTE, f"+refs/heads/{BRANCH}:{REMOTE_REF}",
                 timeout=NET_TIMEOUT)
    if fetch.returncode != 0:
        return {"status": "failed", "message": f"warning: push failed: fetch: {_oneline(fetch)}"}

    unpushed = _git(repo, "rev-list", f"{REMOTE_REF}..{BRANCH}").stdout.split()
    if not unpushed:
        return {"status": "nothing", "message": "nothing to push"}
    others = [s for s in unpushed if not _is_record_commit(repo, s, out_rel)]
    if others:
        shown = ", ".join(sha[:SHORT_SHA_LEN] for sha in others[:MAX_LISTED_COMMITS])
        return {"status": "skipped",
                "message": (f"warning: push skipped: local {BRANCH} has {len(others)} unpushed "
                            f"commit(s) that are not change records ({shown}); push them by hand")}

    invalid = _invalid_records(repo, unpushed, out_rel)
    if invalid is None:
        return {"status": "skipped",
                "message": "warning: push skipped: jsonschema unavailable, cannot validate records"}
    if invalid:
        shown = ", ".join(invalid[:MAX_LISTED_COMMITS])
        return {"status": "skipped",
                "message": f"warning: push skipped: record(s) fail the change-record schema "
                           f"({shown}); fix or drop them by hand"}

    behind = _git(repo, "merge-base", "--is-ancestor", REMOTE_REF, BRANCH).returncode != 0
    if behind:
        problem = _rebase_onto_remote(repo, out_rel)
        if problem:
            return problem

    push = _git(repo, "push", "-q", REMOTE, f"refs/heads/{BRANCH}:refs/heads/{BRANCH}",
                timeout=NET_TIMEOUT)
    if push.returncode != 0:
        return {"status": "failed", "message": f"warning: push failed: {_oneline(push)}"}
    n = len(unpushed)
    return {"status": "pushed",
            "message": f"pushed {n} record commit{'' if n == 1 else 's'} to {REMOTE}/{BRANCH}"}
