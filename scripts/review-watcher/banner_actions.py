#!/usr/bin/env python3
"""Clickable review banner, via alerter. alerts.banner() runs it detached.

  banner_actions.py --title T --message M [--pr N]

Shows a persistent alert (group "reviews", replaces the previous one). alerter
blocks until the user acts or ALERT_TIMEOUT_SECONDS pass. Recent macOS puts the
actions under an "Options" menu: one entry per ACTIONS label, plus "Later" (the
close label). A click on the body runs CLICK_ACTION.

  open   queue an open request (open_requests.add) for the Review Watcher pane,
         which runs review-open, and bring cmux forward

Later, close (x) and timeout do nothing; the reminder still fires on its own
schedule. To offer another action, add a label to ACTIONS and its handler to
HANDLERS.

Exit 0 = alert shown (whatever the answer), 127 = alerter not installed.
Test hooks: REVIEW_WATCHER_ALERTER (alerter binary), REVIEW_WATCHER_OPEN_APP
(command run instead of `open -a cmux`).
"""
import argparse
import os
import shlex
import subprocess
import sys

import alerts
import config
import open_requests

# Options-menu label -> action name (a key of HANDLERS).
ACTIONS = {"Open review": "open"}
LATER_LABEL = "Later"
CLICK_ACTION = "open"
CLICK_ANSWERS = ("@CONTENTCLICKED", "@ACTIONCLICKED")
CLICK_HINT = "Click to open the review, or use Options."


def do_open(args):
    open_requests.add(args.pr)
    app = os.environ.get("REVIEW_WATCHER_OPEN_APP") or "open -a cmux"
    subprocess.run(shlex.split(app), capture_output=True)


HANDLERS = {"open": do_open}


def action_for(answer):
    """Action name for alerter's answer, or None (Later, close, timeout, unknown)."""
    if answer in CLICK_ANSWERS:
        return CLICK_ACTION
    return ACTIONS.get(answer)


def main(argv=None):
    p = argparse.ArgumentParser(description="Review banner with Options actions; a click opens the review")
    p.add_argument("--title", required=True)
    p.add_argument("--message", required=True)
    p.add_argument("--pr", type=int)
    args = p.parse_args(argv)
    alerter = alerts.alerter_bin()
    if not alerter:
        print("banner_actions: alerter not installed (brew install vjeantet/tap/alerter)",
              file=sys.stderr)
        return 127
    r = subprocess.run([alerter, "--title", args.title,
                        "--message", f"{args.message}\n{CLICK_HINT}",
                        "--group", alerts.GROUP, "--actions", ",".join(ACTIONS),
                        "--close-label", LATER_LABEL,
                        "--timeout", str(config.ALERT_TIMEOUT_SECONDS)],
                       capture_output=True, text=True)
    answer = r.stdout.strip()
    action = action_for(answer)
    if action:
        HANDLERS[action](args)
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
