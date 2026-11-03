#!/usr/bin/env python3
# check-protected-paths.py: emit a needs-grill verdict when changed paths touch protected_paths.txt
# What: reads changed paths (args, or stdin lines as from git diff --name-only); prints "needs-grill" plus hits, or "ok"; exit 0 no hit, 1 hit, 2 error
# When: run by the PR gate (wired in Phase 2) as: git diff --name-only <base> | check-protected-paths.py [--list FILE]
import fnmatch
import re
import sys
from pathlib import Path

DEFAULT_LIST = Path(__file__).resolve().parent / "protected_paths.txt"


def load_patterns(path):
    patterns = []
    for line in Path(path).read_text().splitlines():
        line = re.sub(r"\s+#.*$", "", line).strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def is_protected(changed, patterns):
    changed = changed.strip().removeprefix("./")
    for pat in patterns:
        if pat.endswith("/"):
            if changed.startswith(pat):
                return True
        elif fnmatch.fnmatchcase(changed, pat):
            return True
    return False


def main(argv):
    list_file = DEFAULT_LIST
    args = list(argv)
    if "--list" in args:
        i = args.index("--list")
        if i + 1 >= len(args):
            print("error: --list needs a file", file=sys.stderr)
            return 2
        list_file = Path(args[i + 1])
        del args[i:i + 2]
    if not Path(list_file).is_file():
        print(f"error: list file not found: {list_file}", file=sys.stderr)
        return 2
    paths = args if args else sys.stdin.read().splitlines()
    paths = [p.strip() for p in paths if p.strip()]
    patterns = load_patterns(list_file)
    hits = [p for p in paths if is_protected(p, patterns)]
    if hits:
        print("needs-grill")
        for h in hits:
            print(f"  {h}")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
