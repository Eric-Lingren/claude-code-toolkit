"""change_records_commit.py — commit pending change records, and nothing else.

What: finds new or modified `*.json` files directly under the change-records directory
  (attribution/change-records/) that git does not yet have committed AND whose change_id was written by
  this run (never stray or hand-edited files), stages exactly those paths and
  creates ONE commit on the current branch containing only them. Never pushes (that is attribution/change_records_push.py). Other staged or
  unstaged changes in the working tree are left exactly as they were. With nothing pending, no
  commit is made.
When: called by `write-change-records.py --commit` (on demand, and from the weekly usage job via
  attribution/commit-change-records.sh). Kept in its own module so write-change-records.py stays small.

Importable: pending_record_files, commit_change_records.
"""
import subprocess
from pathlib import Path


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                          check=False)


def pending_record_files(repo, out_dir):
    """Repo-relative paths of uncommitted record files (untracked or modified) in out_dir."""
    repo = Path(repo).resolve()
    out_rel = Path(out_dir).resolve().relative_to(repo).as_posix()
    proc = _git(repo, "status", "--porcelain", "-z", "--untracked-files=all", "--", out_rel)
    if proc.returncode != 0:
        raise RuntimeError(f"git status failed: {proc.stderr.strip()}")
    paths = []
    fields = proc.stdout.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            i += 1  # skip the origin path of a rename/copy
        if "D" in status:
            continue
        # Only records directly in the directory: <out_rel>/<name>.json
        if path.endswith(".json") and Path(path).parent.as_posix() == out_rel:
            paths.append(path)
    return sorted(paths)


def commit_change_records(repo, out_dir, change_ids):
    """Commit this run's pending record files in one commit.

    Only files named `<change_id>.json` for the given change_ids (the `written` list of
    write_change_records) are committed. A stray or hand-edited JSON in the directory is left alone.

    Returns {"committed": bool, "sha": str|None, "files": [...]}. Raises RuntimeError when git
    refuses; in that case the paths this call staged are unstaged again.
    """
    repo = Path(repo).resolve()
    wanted = {f"{cid}.json" for cid in change_ids}
    files = [f for f in pending_record_files(repo, out_dir) if Path(f).name in wanted]
    if not files:
        return {"committed": False, "sha": None, "files": []}
    add = _git(repo, "add", "--", *files)
    if add.returncode != 0:
        raise RuntimeError(f"git add failed: {add.stderr.strip()}")
    n = len(files)
    subject = f"Add {n} change record{'' if n == 1 else 's'}"
    body = "\n".join(f"- {Path(f).stem}" for f in files)
    # Trailing paths make this a --only commit: other staged changes stay out of it.
    # --no-verify: the commit only adds generated JSON; a pre-commit hook that git-adds other
    # files would pull them into this commit and the push gate would then reject it.
    commit = _git(repo, "commit", "-q", "--no-verify", "-m", subject, "-m", body, "--", *files)
    if commit.returncode != 0:
        _git(repo, "reset", "-q", "--", *files)
        raise RuntimeError(f"git commit failed: {(commit.stderr or commit.stdout).strip()}")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return {"committed": True, "sha": sha, "files": files}
