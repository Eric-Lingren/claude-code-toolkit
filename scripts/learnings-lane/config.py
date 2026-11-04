#!/usr/bin/env python3
"""Single config block for the learnings lane.

Every limit the lane uses lives here and nowhere else. Other modules import from this file;
they never restate a value.

CLI:  config.py show [key]   print the whole block as JSON, or one value.
"""
import argparse
import json
import sys

# --- the config block -------------------------------------------------------

# The selector stops when this many lane PRs are open.
OPEN_PR_CAP = 5
# A target is eligible with at least this many eligible learnings...
MIN_COUNT = 3
# ...or when its oldest eligible learning is at least this many days old.
MIN_AGE_DAYS = 7
# Learnings sharing a lesson reach a cluster at this group size.
GROUP_THRESHOLD = 3
# A root cause seen in at least this many distinct targets becomes an idea issue.
CROSS_TARGET_MIN = 3
# Lane PRs carry this label; their head branch is LANE_BRANCH_PREFIX + target.
LANE_LABEL = "learnings-lane"
LANE_BRANCH_PREFIX = "learnings-lane/"
# A target whose run ended with no committed cluster (needs-grill, no change, draft failed) is not re-run for this many days.
NO_COMMIT_COOLDOWN_DAYS = 7
# A headless run for one target is killed after this long; the kick logs it and moves on.
TARGET_TIMEOUT_MINUTES = 30
# At most this many targets run headless at once.
MAX_CONCURRENT_TARGETS = 2
# The after-merge boot check (headless claude -p from a worktree on main) is killed after this long.
BOOT_TIMEOUT_SECONDS = 120
# Every unattended `claude -p` (validator, drafter, clusterer, boot check) gets only read tools; anything else
# is denied outright (dontAsk never prompts, never falls back to the worktree's settings.local.json allow rules).
CLAUDE_RESTRICT_ARGS = [
    "--allowedTools", "Read,Grep,Glob",
    "--disallowedTools", "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Task,Agent",
    "--permission-mode", "dontAsk",
]
# The smoke layer of the gate is killed after this many seconds.
SMOKE_TIMEOUT_SECONDS = 300
# A lane branch may touch at most this many files.
MAX_LANE_FILES = 3
# learnings_push: total pushes (first plus fetch/rebase/push retries).
MAX_PUSH_ATTEMPTS = 3
# learnings_push: seconds for fetch/push; an unattended job must never hang.
PUSH_NET_TIMEOUT_SECONDS = 120
# learnings_push: files/commits named in a message.
MAX_LISTED = 5
# learnings_push: sha chars shown in messages.
SHORT_SHA_LEN = 10
# `gh ... --limit` values.
GH_LIST_LIMIT_OPEN_PRS = 100
GH_LIST_LIMIT_ALL_PRS = 200
GH_LIST_LIMIT_ISSUES = 20


def block():
    return {k: v for k, v in globals().items() if k.isupper()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sh = sub.add_parser("show")
    sh.add_argument("key", nargs="?")
    args = ap.parse_args(argv)
    b = block()
    if args.key:
        if args.key not in b:
            print(f"unknown key: {args.key}", file=sys.stderr)
            return 1
        print(json.dumps(b[args.key]))
    else:
        print(json.dumps(b, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
