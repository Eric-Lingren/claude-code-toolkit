#!/usr/bin/env python3
"""iterate_commit.py <agent> <check> [--dry-run] — commit a KEPT fix and push it straight to main.

Commits exactly one commit: "agent-bench(<agent>): <check> fix", containing only
  the agent edit (files recorded for the kept iteration), claude-code-shared/evals/<agent>/contract.json,
  cases.jsonl and scores.json (whichever exist and differ from HEAD).
Then pushes with ~/.dotfiles/.scripts/gxpush --push-only --auto (the commit message is fixed here, so
gxpush must not stage or re-message anything). Prints the new SHA on the last line; iterate.py records it
in the agent's history.

--dry-run prints the commit message, the file list and the exact git/gxpush commands and executes nothing.
Live mode refuses unless the current branch is main and the staged set is exactly the listed files.
Files come from the iteration state: iterate.py writes them via `--files a,b` (repo-root-relative).
"""
import os
import subprocess
import sys

import bench_lib as L

GXPUSH = os.path.expanduser("~/.dotfiles/.scripts/gxpush")


def commit_files(agent, edited):
    root = L.repo_root()
    ev = os.path.relpath(os.path.join(L.SHARED_DIR, "evals", agent), root)
    cand = list(edited) + [os.path.join(ev, n) for n in ("contract.json", "cases.jsonl", "scores.json")]
    out = []
    for f in cand:
        if f in out or not os.path.exists(os.path.join(root, f)):
            continue
        out.append(f)
    return out


def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    dry = "--dry-run" in sys.argv
    files_arg = next((x.split("=", 1)[1] for x in sys.argv if x.startswith("--files=")), "")
    if len(a) != 2 or not files_arg:
        sys.exit(__doc__)
    agent, check = a
    root = L.repo_root()
    files = commit_files(agent, [f for f in files_arg.split(",") if f])
    msg = f"agent-bench({agent}): {check} fix"
    cmds = [["git", "add", "--"] + files, ["git", "commit", "-m", msg, "--"] + files, [GXPUSH, "--push-only", "--auto"]]
    print(f"message: {msg}")
    print("files:\n" + "\n".join(f"  {f}" for f in files))
    for c in cmds:
        print("cmd: " + " ".join(c))
    if dry:
        print("DRY RUN: nothing executed (no commit, no push).")
        print("sha: (dry-run)")
        return
    branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True, cwd=root).strip()
    if branch != "main":
        sys.exit(f"refusing: current branch is {branch!r}, a kept fix is committed on main")
    run = lambda c: subprocess.run(c, cwd=root, check=True)
    run(cmds[0])
    staged = subprocess.check_output(["git", "diff", "--cached", "--name-only"], text=True, cwd=root).split()
    if not staged or not set(staged) <= set(files):
        subprocess.run(["git", "reset", "-q"], cwd=root)
        sys.exit(f"refusing: staged set {staged} differs from the allowed files {files}")
    run(cmds[1])
    run(cmds[2])
    print("sha: " + subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=root).strip())


if __name__ == "__main__":
    main()
