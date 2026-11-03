#!/usr/bin/env python3
"""Live queue view for the Review Watcher cmux workspace.

Every QUEUE_VIEW_REFRESH_SECONDS: list each queue entry (PR, repo, title, state
queued/running/ready/reminded, office-minute wait), oldest first, then refresh the
cmux pill. The screen is only cleared and redrawn when that text changed, so a
half-typed command is not wiped for nothing. A "Next:" line says whether to wait
or run review-open. Runs inside cmux, where the socket is reachable even when
launchd cannot reach it.

Between refreshes it reads typed lines and runs the review commands only:
review-open [PR], review-restore PR, review-rerun PR and review-help (the plain
`reviews` listing would repeat the view). They run through reviews.sh with
REVIEW_WATCHER_PANE=1, so review-open opens the review in its own workspace and
never in this pane. Their output shows under the queue until the next command;
an empty line clears it. Anything else is refused.

It also polls open_requests every POLL_SECONDS: a click on a review banner
lands here and runs as review-open [PR], since only processes inside cmux can
open the review workspace.

  queue_view.py          loop forever
  queue_view.py --once   render once (no clear), update the pill, exit
"""
import os
import select
import subprocess
import sys
import time

import alerts
import workspace
import config
import events
import open_requests

HERE = os.path.dirname(os.path.abspath(__file__))
REVIEWS_SH = os.path.join(HERE, "reviews.sh")
POLL_SECONDS = 1
SHOWN = ("queued", "running", "ready", "reminded")
READY = ("ready", "reminded")
HINT_READY = ("Next: type review-open below (oldest ready review) or review-open <PR number>. "
              "It opens in its own workspace.")
HINT_WAIT = "Next: nothing. The review runs headless and a banner fires when it is ready."
HINT_EMPTY = (f"Next: nothing. The watcher checks for review requests every "
              f"{config.CHECK_INTERVAL_MINUTES}m in office hours.")
HELP = (f"Commands here: review-open [PR], review-restore PR, review-rerun PR, review-help. "
        f"Refreshes every {config.QUEUE_VIEW_REFRESH_SECONDS}s.")
REFUSED = ("Only review-open [PR], review-restore PR, review-rerun PR and review-help run here. "
           "Use another terminal for anything else.")
# typed command -> reviews.sh args (review-open may add a PR number; restore/rerun need one)
NEEDS_PR = ("review-restore", "review-rerun")
COMMANDS = {"review-open": ["open"], "review-restore": ["restore"], "review-rerun": ["rerun"],
            "review-help": ["help"],
            "help": ["help"], "h": ["help"], "?": ["help"]}


def fmt_wait(minutes):
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def render(entries, message=""):
    rows = sorted((e for e in entries if e["state"] in SHOWN),
                  key=lambda e: events.parse_ts(e["requested_at"]))
    lines = ["PR review queue", ""]
    if not rows:
        lines.append("(queue empty)")
    for e in rows:
        lines.append(f"{e['repo']}#{e['pr']}  {e['state']:<8}  waited {fmt_wait(e['wait_office_minutes'])}"
                     f"  {e.get('title') or ''}".rstrip())
    if any(e["state"] in READY for e in rows):
        hint = HINT_READY
    else:
        hint = HINT_WAIT if rows else HINT_EMPTY
    lines += ["", hint, HELP]
    if message:
        lines += ["", message.rstrip()]
    return "\n".join(lines)


def parse(line):
    """reviews.sh args for a typed line, or None if refused."""
    words = line.split()
    if not words or words[0] not in COMMANDS:
        return None
    args, rest = list(COMMANDS[words[0]]), words[1:]
    if not rest:
        return None if words[0] in NEEDS_PR else args
    if words[0] in ("review-open",) + NEEDS_PR and len(rest) == 1 and rest[0].lstrip("#").isdigit():
        return args + [rest[0].lstrip("#")]
    return None


def run_command(line):
    """Text to show under the queue for one typed line ("" clears it)."""
    if not line.strip():
        return ""
    args = parse(line)
    if args is None:
        return f"{line.strip()}: {REFUSED}"
    try:
        p = subprocess.run(["bash", REVIEWS_SH, *args], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60,
                           env=dict(os.environ, REVIEW_WATCHER_PANE="1"))
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{line.strip()}: failed ({exc})"
    return (p.stdout + p.stderr).strip() or f"{line.strip()}: done"


def refresh(clear, message="", last=None):
    """Render; when clearing, redraw only if the text differs from last. Returns the text."""
    entries = events.reduce_events(events.read())
    text = render(entries, message)
    if not clear:
        print(text, flush=True)
    elif text != last:
        sys.stdout.write("\033[H\033[2J" + text + "\n\n> ")
        sys.stdout.flush()
    try:
        alerts.update_pill(entries)
    except Exception:
        pass
    # Inside cmux the socket is reachable (launchd's is not), so the view also
    # keeps the Review Watcher workspace pinned at the top of the sidebar.
    try:
        workspace.ensure()
    except Exception:
        pass
    return text


class Input:
    """Typed lines from stdin with a timeout. After EOF it only waits out the timeout."""

    def __init__(self, stream):
        self.stream = stream
        self.open = True

    def read(self, timeout):
        if self.open:
            try:
                ready, _, _ = select.select([self.stream], [], [], timeout)
            except (OSError, ValueError):
                ready, self.open = [], False
            if not ready and self.open:
                return None
            if ready:
                line = self.stream.readline()
                if line:
                    return line
                self.open = False
        time.sleep(timeout)
        return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--once" in argv:
        refresh(False)
        return 0
    typed = Input(sys.stdin)
    message, last, due = "", None, 0.0
    while True:
        if time.monotonic() >= due:
            last = refresh(True, message, last)
            due = time.monotonic() + config.QUEUE_VIEW_REFRESH_SECONDS
        line = typed.read(POLL_SECONDS)
        todo = [] if line is None else [line]
        todo += [f"review-open {sel}".strip() for sel in open_requests.take()]
        if todo:
            message = "\n".join(m for m in map(run_command, todo) if m)
            last, due = None, 0.0


if __name__ == "__main__":
    sys.exit(main())
