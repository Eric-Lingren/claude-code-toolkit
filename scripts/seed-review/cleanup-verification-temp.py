#!/usr/bin/env python3
"""cleanup-verification-temp.py - delete to-seed verification temp files.

Removes the temp files the to-seed verification stage writes (cleaned
transcript, seed draft, refutations, evidence pack). Deletes in Python, so no
shell `rm` runs over variables and the destructive-fs hook is never involved.

Missing paths and empty-string args are skipped. Some files never exist when
the judge stage was skipped. Every path must sit under a temp root (/tmp or
the system temp dir, symlinks resolved). Directories and other non-regular
files are refused. All args are validated before anything is deleted.

Usage:
  python3 cleanup-verification-temp.py <path> [<path> ...]

Output:
  OK: <R> removed, <M> missing

Exit codes:
  0  Success - every existing path removed
  1  Error - no args, path outside a temp root, directory, or unlink failure
"""

import os
import stat
import sys
import tempfile


def temp_roots():
    """Resolved temp roots a path may live under."""
    return {os.path.realpath(p) for p in ("/tmp", tempfile.gettempdir())}


def resolve(path: str) -> str:
    """Resolve the parent dir, keep the basename (so a symlink is not followed)."""
    abspath = os.path.abspath(path)
    return os.path.join(os.path.realpath(os.path.dirname(abspath)), os.path.basename(abspath))


def under_temp(resolved: str, roots) -> bool:
    return any(resolved.startswith(root + os.sep) for root in roots)


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)
    if not args:
        print(
            "ERROR: expected at least 1 path.\n"
            "Usage: cleanup-verification-temp.py <path> [<path> ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    roots = temp_roots()
    targets = []
    missing = 0
    for arg in args:
        if not arg:
            missing += 1
            continue
        resolved = resolve(arg)
        if not under_temp(resolved, roots):
            print(f"ERROR: refusing path outside temp root: {arg}", file=sys.stderr)
            sys.exit(1)
        try:
            mode = os.lstat(resolved).st_mode
        except FileNotFoundError:
            missing += 1
            continue
        if stat.S_ISDIR(mode):
            print(f"ERROR: refusing to delete directory: {arg}", file=sys.stderr)
            sys.exit(1)
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            print(f"ERROR: refusing non-regular file: {arg}", file=sys.stderr)
            sys.exit(1)
        targets.append(resolved)

    removed = 0
    for resolved in targets:
        try:
            os.unlink(resolved)
            removed += 1
        except FileNotFoundError:
            missing += 1
        except OSError as e:
            print(f"ERROR: could not delete {resolved}: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"OK: {removed} removed, {missing} missing")


if __name__ == "__main__":
    main()
