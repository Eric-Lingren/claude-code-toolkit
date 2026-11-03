#!/usr/bin/env python3
"""Render local.review-watcher.plist.template to stdout.

Usage: render-plist.py LABEL WATCHER_SH INTERVAL_MINUTES STATE_DIR
Every path is absolute because launchd does not load the shell PATH.
REVIEW_WATCHER_HOME, when set, is carried into the job environment.
"""
import os
import sys
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    label, watcher, minutes, state_dir = argv
    home = os.path.expanduser("~")
    path = ":".join(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                     "/usr/sbin", "/sbin", os.path.join(home, ".local/bin")])
    extra = ""
    if os.environ.get("REVIEW_WATCHER_HOME"):
        extra = ("    <key>REVIEW_WATCHER_HOME</key>\n    <string>%s</string>"
                 % escape(os.environ["REVIEW_WATCHER_HOME"]))
    subs = {
        "LABEL": label, "WATCHER_SH": watcher, "HOME": home, "PATH": path,
        "EXTRA_ENV": extra, "INTERVAL_SECONDS": str(int(minutes) * 60),
        "OUT_LOG": os.path.join(state_dir, "launchd.out.log"),
        "ERR_LOG": os.path.join(state_dir, "launchd.err.log"),
    }
    with open(os.path.join(HERE, "local.review-watcher.plist.template")) as fh:
        text = fh.read()
    for k, v in subs.items():
        text = text.replace("@%s@" % k, v if k == "EXTRA_ENV" else escape(v))
    sys.stdout.write(text)


if __name__ == "__main__":
    main(sys.argv[1:])
