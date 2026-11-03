#!/usr/bin/env python3
"""Single config block for the PR review watcher.

Every limit, window and path the watcher family uses lives here and nowhere
else. Other modules import from this file; they never restate a value.

CLI:  config.py show [key]   print the whole block as JSON, or one value.

Test hooks: REVIEW_WATCHER_HOME relocates the state directory (log + state file);
REVIEW_WATCHER_CLAUDE_BIN swaps the claude binary for a stub; REVIEW_WATCHER_CHECKOUTS
(JSON object repo -> path) replaces REPO_CHECKOUTS.
"""
import argparse
import json
import os
import sys
from datetime import time

# --- the config block -------------------------------------------------------

TIMEZONE = "America/Los_Angeles"
CHECK_INTERVAL_MINUTES = 10
# Watcher runs only inside this window (weekdays, local to TIMEZONE).
ACTIVE_WINDOW_START = time(8, 50)
ACTIVE_WINDOW_END = time(18, 0)
# Office hours used to measure waits (weekdays, local to TIMEZONE).
OFFICE_HOURS_START = time(9, 0)
OFFICE_HOURS_END = time(18, 0)
WEEKDAYS = (0, 1, 2, 3, 4)  # Monday=0
# A review-rerun outside that window keeps the watcher ticking this long (manual_window.py).
MANUAL_RUN_MINUTES = 90
MAX_CONCURRENT_REVIEWS = 2
REVIEW_TIMEOUT_MINUTES = 45
# Review Watcher workspace queue view redraw interval (env override is a test hook).
QUEUE_VIEW_REFRESH_SECONDS = int(os.environ.get("QUEUE_VIEW_REFRESH_SECONDS") or 30)
REMINDER_AFTER_OFFICE_MINUTES = 60
# An alerter review banner (Options actions) closes itself after this long.
ALERT_TIMEOUT_SECONDS = 4 * 60 * 60
# launchd does not load the shell PATH, so the claude binary is absolute.
CLAUDE_BIN = os.environ.get("REVIEW_WATCHER_CLAUDE_BIN") or os.path.expanduser(
    "~/.local/bin/claude"
)
# The `wt` worktree engine. Review worktrees live where `wt <branch>` puts them.
WORKTREE_ENGINE = os.environ.get("REVIEW_WATCHER_WORKTREE_ENGINE") or os.path.expanduser(
    "~/.dotfiles/.scripts/worktree"
)
# fnm's default node, put on the headless run's PATH so node-based hooks work under
# launchd (which does not load the shell's fnm setup).
NODE_BIN_DIR = os.path.join(
    os.environ.get("FNM_DIR") or os.path.expanduser("~/.local/share/fnm"),
    "aliases", "default", "bin",
)
# Office profile (cco): review requests are work PRs.
CLAUDE_CONFIG_DIR = os.path.expanduser("~/.cco")
STATE_DIR = os.environ.get("REVIEW_WATCHER_HOME") or os.path.expanduser(
    "~/.local/state/review-watcher"
)
RUNS_DIR = os.path.join(STATE_DIR, "runs")
# Local clones the watcher reviews from (never the working checkout: each run gets
# its own detached git worktree under RUNS_DIR). Defaults to the office-profile (cco)
# repos in repo-policy.json. REVIEW_WATCHER_CHECKOUTS (JSON object) replaces the map for tests.
def _policy_checkouts():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "resources", "repo-policy.json")
    try:
        with open(path) as f:
            policy = json.load(f)
    except (OSError, ValueError):
        return {}
    return {
        repo: os.path.expanduser(entry["local_path"])
        for repo, entry in policy.items()
        if isinstance(entry, dict) and entry.get("account") == "cco" and entry.get("local_path")
    }


REPO_CHECKOUTS = (
    json.loads(os.environ["REVIEW_WATCHER_CHECKOUTS"])
    if os.environ.get("REVIEW_WATCHER_CHECKOUTS")
    else _policy_checkouts()
)
EVENT_LOG_PATH = os.path.join(STATE_DIR, "events.jsonl")
STATE_PATH = os.path.join(STATE_DIR, "state.json")

# --- end of block -----------------------------------------------------------

KEYS = (
    "TIMEZONE", "CHECK_INTERVAL_MINUTES", "ACTIVE_WINDOW_START",
    "ACTIVE_WINDOW_END", "OFFICE_HOURS_START", "OFFICE_HOURS_END", "WEEKDAYS",
    "MANUAL_RUN_MINUTES",
    "MAX_CONCURRENT_REVIEWS", "REVIEW_TIMEOUT_MINUTES", "QUEUE_VIEW_REFRESH_SECONDS",
    "REMINDER_AFTER_OFFICE_MINUTES", "ALERT_TIMEOUT_SECONDS", "WORKTREE_ENGINE", "NODE_BIN_DIR", "CLAUDE_BIN", "CLAUDE_CONFIG_DIR", "STATE_DIR",
    "RUNS_DIR", "REPO_CHECKOUTS", "EVENT_LOG_PATH", "STATE_PATH",
)


def as_dict():
    out = {}
    for k in KEYS:
        v = globals()[k]
        out[k] = v.strftime("%H:%M") if isinstance(v, time) else v
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Review watcher config block")
    sub = p.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show", help="print config as JSON, or one key")
    show.add_argument("key", nargs="?", choices=KEYS)
    args = p.parse_args(argv)
    d = as_dict()
    print(json.dumps(d[args.key]) if args.key else json.dumps(d, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
