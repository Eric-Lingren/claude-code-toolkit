#!/usr/bin/env python3
"""rewrite-old-paths.py - rewrite old seed-bank / bank paths in scaffolding docs.

Usage:
  rewrite-old-paths.py [--apply] [--root DIR] [--file PATH ...]

Dry-run by default; --apply writes. --file PATH rewrites only that file
(any path, tracked or not; exemptions do not apply). Default scope is
git-tracked files under docs/seeds, docs/tasks (task JSON and .logs) and
docs/handoffs. Untracked files are skipped and listed.

Exactly four patterns are replaced:
  claude-code-shared/seed-bank/changes, seed-bank/changes -> attribution/change-records
  scripts/bank/   -> scripts/attribution/
  bank revert     -> change-revert
  bank.sh         -> change-revert.sh

Never touched: unified-learnings.jsonl, hook-audit.jsonl, change records,
the seed describing this move, and its task file and logs.

The report lists remaining bare 'seed-bank' / 'bank' mentions in Phase 1
docs for manual review. Exit 0 on success.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

PATTERN = re.compile(
    r"(?<![\w/-])claude-code-shared/seed-bank/changes"
    r"|(?<![\w-])seed-bank/changes"
    r"|(?<![\w-])scripts/bank/"
    r"|(?<![\w-])bank revert"
    r"|(?<![\w-])bank\.sh"
)
REPL = {
    "seed-bank/changes": "attribution/change-records",
    "scripts/bank/": "scripts/attribution/",
    "bank revert": "change-revert",
    "bank.sh": "change-revert.sh",
}
SCOPE = ("docs/seeds/", "docs/tasks/", "docs/handoffs/")
EXEMPT_NAMES = {"unified-learnings.jsonl", "hook-audit.jsonl"}
EXEMPT_REL = {
    "docs/seeds/20261103-2223-change-records-apart-from-seed-bank.json",
    "docs/tasks/20261103-2230-change-records-apart-from-seed-bank.json",
}
EXEMPT_PREFIX = (
    "docs/tasks/.logs/20261103-2230-change-records-apart-from-seed-bank/",
    "attribution/change-records/",
    "claude-code-shared/seed-bank/changes/",
)
REVIEW = re.compile(r"seed-bank|\bbank\b", re.I)


def repl(m):
    s = m.group(0)
    if s.endswith("seed-bank/changes"):
        return REPL["seed-bank/changes"]
    return REPL[s]


def git_lines(root, *args):
    out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout
    return [x for x in out.split("\n") if x]


def exempt(rel):
    return rel in EXEMPT_REL or Path(rel).name in EXEMPT_NAMES or rel.startswith(EXEMPT_PREFIX)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rewrite old seed-bank/bank paths.")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--root", default=None, help="repo root (default: git toplevel of cwd)")
    ap.add_argument("--file", action="append", default=[], help="rewrite only this file")
    a = ap.parse_args(argv)
    if a.root:
        root = Path(a.root)
    else:
        root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                   capture_output=True, text=True, check=True).stdout.strip())
    root = root.resolve()

    changed, skipped, review = [], [], []
    if a.file:
        targets = [Path(f) for f in a.file]
    else:
        tracked = set(git_lines(root, "ls-files", "--", *SCOPE))
        on_disk = set()
        for s in SCOPE:
            d = root / s
            if d.is_dir():
                for p in d.rglob("*"):
                    if p.is_file():
                        on_disk.add(p.relative_to(root).as_posix())
        skipped = sorted(on_disk - tracked)
        targets = [root / r for r in sorted(tracked) if not exempt(r) and (root / r).is_file()]

    for p in targets:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new, n = PATTERN.subn(repl, text)
        rp = p.resolve()
        rel = rp.relative_to(root).as_posix() if rp.is_relative_to(root) else str(p)
        if n:
            changed.append((rel, n))
            if a.apply:
                p.write_text(new, encoding="utf-8")
        if not a.file and "phase-1" in rel:
            for i, line in enumerate(new.splitlines(), 1):
                if REVIEW.search(line):
                    review.append((rel, i, line.strip()[:160]))

    print(f"mode: {'apply' if a.apply else 'dry-run'}")
    print(f"files changed: {len(changed)} (replacements: {sum(n for _, n in changed)})")
    for rel, n in changed:
        print(f"  {n:4d}  {rel}")
    print(f"skipped untracked: {len(skipped)}")
    for rel in skipped:
        print(f"  {rel}")
    print(f"manual review (bare seed-bank/bank in Phase 1 docs): {len(review)}")
    for rel, i, line in review:
        print(f"  {rel}:{i}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
