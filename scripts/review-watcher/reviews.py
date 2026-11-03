#!/usr/bin/env python3
"""Ready-review listing and resolution for reviews.sh.

  reviews.py list            ready queue as JSON rows, oldest request first
  reviews.py show            human-readable listing of the same rows
  reviews.py pick SELECTION  JSON row for a list index (1 = oldest) or PR number
                             (exit 1 if nothing matches)
  reviews.py pick --pr N     JSON row for PR number N only (never a list index)
  reviews.py config          claude bin and config dir as JSON
  reviews.py help            what each queue state means and what to do next
  reviews.py restore N       put PR N's opened (approved) review back in the ready list
  reviews.py rerun N         review PR N again from scratch with the current setup:
                             retire its ready review (dismissed), queue a new request,
                             and start the watcher job now (launchctl kickstart).
                             Outside office hours it keeps the watcher on for
                             MANUAL_RUN_MINUTES (manual_window.py) so the run starts.

A row: index, repo, pr, request_id, title, wait_office_minutes, session_id, cwd.
Ready means state ready or reminded. Queue state comes from events.py; session
and cwd come from the review_ready event data, falling back to
<STATE_DIR>/runs/<request_id>/{session_id,wt}. Title comes from event data, else
one read-only `gh pr view` (binary overridable with REVIEW_WATCHER_GH).
"""
import json
import os
import subprocess
import sys

import config
import events
import manual_window
from office_minutes import TZ, in_active_window

READY_STATES = ("ready", "reminded")


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _gh_title(repo, pr):
    gh = os.environ.get("REVIEW_WATCHER_GH", "gh")
    try:
        r = subprocess.run(
            [gh, "pr", "view", str(pr), "--repo", repo, "--json", "title", "-q", ".title"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def ready_rows():
    log = events.read()
    ready_data = {}
    for ev in log:
        if ev["type"] == "review_ready":
            ready_data[ev["request_id"]] = ev.get("data") or {}
    entries = [e for e in events.reduce_events(log) if e["state"] in READY_STATES]
    entries.sort(key=lambda e: events.parse_ts(e["requested_at"]))
    rows = []
    for i, e in enumerate(entries, 1):
        rid = e["request_id"]
        data = ready_data.get(rid, {})
        run_dir = os.path.join(config.STATE_DIR, "runs", rid)
        rows.append({
            "index": i, "repo": e["repo"], "pr": e["pr"], "request_id": rid,
            "title": (data.get("title") or e.get("title")
                      or _gh_title(e["repo"], e["pr"]) or "(title unavailable)"),
            "wait_office_minutes": e["wait_office_minutes"],
            "session_id": data.get("session_id") or _read(os.path.join(run_dir, "session_id")),
            "cwd": data.get("cwd") or os.path.join(run_dir, "wt"),
        })
    return rows


def _latest(entries):
    return max(entries, key=lambda e: events.parse_ts(e["requested_at"])) if entries else None


def restore(pr):
    """Put the PR's latest opened review back to ready (same session and worktree)."""
    log = events.read()
    e = _latest([e for e in events.reduce_events(log)
                 if e["pr"] == pr and e["state"] == "approved"])
    if e is None:
        print(f"review-restore: no opened review for PR {pr}", file=sys.stderr)
        return 1
    data = {}
    for ev in log:
        if ev["type"] == "review_ready" and ev["request_id"] == e["request_id"]:
            data = ev.get("data") or {}
    events.append("review_ready", e["repo"], e["pr"], e["request_id"], data=data)
    print(f"Restored {e['repo']}#{pr} to the ready list.")
    return 0


def kick_watcher():
    """Run the launchd watcher job now. launchd never runs two copies of a job."""
    launchctl = os.environ.get("REVIEW_WATCHER_LAUNCHCTL", "launchctl")
    label = os.environ.get("REVIEW_WATCHER_LABEL", "local.review-watcher")
    try:
        r = subprocess.run([launchctl, "kickstart", f"gui/{os.getuid()}/{label}"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def rerun(pr):
    """Queue a fresh review of the PR. Its ready review is dismissed; an opened one is
    left alone (its workspace may still be open on that worktree)."""
    log = events.read()
    mine = [e for e in events.reduce_events(log) if e["pr"] == pr]
    busy = [e for e in mine if e["state"] in ("queued", "running")]
    if busy:
        print(f"review-rerun: PR {pr} is already {busy[0]['state']}", file=sys.stderr)
        return 1
    last = _latest(mine)
    if last is None:
        print(f"review-rerun: no review for PR {pr} to rerun", file=sys.stderr)
        return 1
    for e in mine:
        if e["state"] in READY_STATES:
            events.append("dismissed", e["repo"], e["pr"], e["request_id"],
                          data={"reason": "rerun"})
    seen = {}
    for ev in log:
        if ev["type"] == "request_seen" and ev["request_id"] == last["request_id"]:
            seen = ev
    base = last["request_id"].split("-rerun")[0]
    n = 1 + sum(1 for e in mine if e["request_id"].startswith(base + "-rerun"))
    events.append("request_seen", last["repo"], pr, f"{base}-rerun{n}",
                  head_sha=seen.get("head_sha"), data=seen.get("data") or {})
    at = events.parse_ts(os.environ.get("REVIEW_WATCHER_NOW") or events.now_iso())
    after_hours = None if in_active_window(at) else manual_window.extend(at)
    started = kick_watcher()
    print(f"Queued a fresh review of {last['repo']}#{pr}. "
          + ("The watcher is starting it now." if started
             else "It starts on the next watcher tick."))
    if after_hours:
        end = after_hours.astimezone(TZ).strftime("%H:%M")
        print(f"Outside office hours: the watcher stays on until {end} for this run.")
    return 0


def fmt_wait(minutes):
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def help_text():
    hm = lambda t: t.strftime("%H:%M")
    return f"""PR review watcher

Every {config.CHECK_INTERVAL_MINUTES}m in office hours ({hm(config.OFFICE_HOURS_START)}-{hm(config.OFFICE_HOURS_END)}, weekdays) the watcher looks for
PRs that request your review and runs /pr-code-review on each one headless.
Nothing is posted to GitHub until you open the review and approve posting.

Queue states (Review Watcher pane):
  queued    waiting for a free slot (max {config.MAX_CONCURRENT_REVIEWS} at once). Wait.
  running   Claude is reviewing it now (timeout {config.REVIEW_TIMEOUT_MINUTES}m). Wait.
  ready     findings are ready. A banner fires and the pill shows "<n> ready".
            Run review-open.
  reminded  still ready after {config.REMINDER_AFTER_OFFICE_MINUTES} office minutes, one reminder sent.
            Run review-open.
  failed    the run failed. A banner names the reason.
  dismissed retired by review-rerun (not shown).

Commands:
  reviews            list ready reviews, oldest first
  review-open        open the oldest ready review's Claude session in its own workspace
                     (the PR branch's wt worktree with split panes, when it has one)
  review-open <PR>   same, for that PR number
  review-restore <PR>  put a review you opened back in the ready list
  review-rerun <PR>  review that PR again from scratch with the current setup
                     (after hours too: the watcher stays on {config.MANUAL_RUN_MINUTES}m for it)
  review-pane        recreate or re-pin the Review Watcher workspace
  review-queue       restart the live queue view in this terminal
  review-help        this help

review-open, review-restore, review-rerun and review-help also work typed in the watcher pane.
In the opened session, read the findings, then tell Claude to post them (or not).
Posting still asks for approval."""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else ""
    if cmd == "list":
        print(json.dumps(ready_rows()))
        return 0
    if cmd == "show":
        rows = ready_rows()
        if not rows:
            print("No reviews ready.")
            return 0
        for r in rows:
            print(f"{r['index']:>2}. {r['repo']}#{r['pr']}  {r['title']}  "
                  f"(waiting {fmt_wait(r['wait_office_minutes'])})")
        print("\nreview-open             open the oldest in its Claude session"
              "\nreview-open <PR number> open that PR's review")
        return 0
    if cmd == "pick" and len(argv) == 3 and argv[1] == "--pr" and argv[2].isdigit():
        n = int(argv[2])
        hit = next((r for r in ready_rows() if r["pr"] == n), None)
        if hit is None:
            return 1
        print(json.dumps(hit))
        return 0
    if cmd == "pick" and len(argv) == 2:
        rows = ready_rows()
        sel = argv[1]
        hit = None
        if sel.isdigit():
            n = int(sel)
            hit = (next((r for r in rows if r["index"] == n), None)
                   or next((r for r in rows if r["pr"] == n), None))
        if hit is None:
            return 1
        print(json.dumps(hit))
        return 0
    if cmd == "config":
        print(json.dumps({"claude_bin": config.CLAUDE_BIN,
                          "config_dir": config.CLAUDE_CONFIG_DIR}))
        return 0
    if cmd == "help":
        print(help_text())
        return 0
    if cmd in ("restore", "rerun") and len(argv) == 2 and argv[1].lstrip("#").isdigit():
        pr = int(argv[1].lstrip("#"))
        return restore(pr) if cmd == "restore" else rerun(pr)
    print("usage: reviews.py list|show|pick SELECTION|pick --pr N|config|help"
          "|restore N|rerun N", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
