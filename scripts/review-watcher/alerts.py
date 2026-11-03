#!/usr/bin/env python3
"""Alerts for the PR review watcher: sidebar pill, banners, reminder, failure notice.

The event log stays the source of truth. Everything here is derived from the
reduced queue, so a dismissed banner loses nothing. The only extra state is
STATE_DIR/alerts.json, a dedup list of request ids already announced as ready or
failed, so a banner fires once per readiness, not once per tick.

run(now)  (called at the end of each watcher tick)
  - pill: `cmux set-status reviews "<n> ready"` on the Review Watcher workspace, where n
    counts requests in ready or reminded state; cleared when n is 0.
  - banner (group "reviews", replaces the previous one): "PR #N review ready:
    <title>" for one request, "<k> reviews ready" for several in one tick. Ready
    and reminder banners use alerter when installed: banner_actions.py runs
    detached and shows a persistent banner with Options actions (Open review,
    Later); a click on it also opens the review (see that file). Without
    alerter, and for failure banners, it is a plain notify.sh banner.
  - reminder: a request ready for REMINDER_AFTER_OFFICE_MINUTES office minutes gets
    one banner and a `reminded` event (state reminded), then never again.
  - failure: "Review failed: PR #N (<reason>)" once per failed request.
Clicking a plain banner activates cmux and selects the Review Watcher workspace when it exists.

Every cmux call runs under a short timeout and never raises: a dead socket must
not fail the tick. cmux notify is deliberately not used.

CLI:  alerts.py pill     update the pill only (for the queue view)
      alerts.py run      run the full alerts step now

Test hooks (env): REVIEW_WATCHER_CMUX, REVIEW_WATCHER_NOTIFY, REVIEW_WATCHER_ALERTER
replace the binaries (REVIEW_WATCHER_ALERTER="" means no alerter).
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

import config
import events
from office_minutes import office_minutes

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_TITLE = "Review Watcher"
# Earlier title. workspace.ensure() renames a workspace still carrying it.
LEGACY_WORKSPACE_TITLES = ("Reviews",)
NOTIFY_DEFAULT = os.path.normpath(os.path.join(HERE, "..", "notify", "notify.sh"))
ALERTER_FALLBACK = "/opt/homebrew/bin/alerter"
BANNER_ACTIONS = os.path.join(HERE, "banner_actions.py")
CMUX_FALLBACK = "/Applications/cmux.app/Contents/Resources/bin/cmux"
CMUX_TIMEOUT_SECONDS = 3
NOTIFY_TIMEOUT_SECONDS = 10
GROUP = "reviews"
PILL_KEY = "reviews"
PILL_ICON = "eye"
PILL_COLOR = "#3B82F6"
READY_STATES = ("ready", "reminded")


def alerts_path():
    return os.path.join(config.STATE_DIR, "alerts.json")


def cmux_bin():
    override = os.environ.get("REVIEW_WATCHER_CMUX")
    if override:
        return override
    return shutil.which("cmux") or (CMUX_FALLBACK if os.path.exists(CMUX_FALLBACK) else None)


def alerter_bin():
    if "REVIEW_WATCHER_ALERTER" in os.environ:
        return os.environ["REVIEW_WATCHER_ALERTER"] or None
    return shutil.which("alerter") or (ALERTER_FALLBACK if os.path.exists(ALERTER_FALLBACK) else None)


def notify_bin():
    return os.environ.get("REVIEW_WATCHER_NOTIFY") or NOTIFY_DEFAULT


def cmux(*args):
    """Run a cmux command. Returns stdout, or None on any failure or timeout."""
    binary = cmux_bin()
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, *args], capture_output=True, text=True,
                              timeout=CMUX_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def workspace_titled(title, listing):
    """Ref of the first workspace in a `cmux list-workspaces` listing whose line has title."""
    for line in (listing or "").splitlines():
        if title in line:
            m = re.search(r"workspace:\d+", line)
            if m:
                return m.group(0)
    return None


def reviews_workspace():
    """The watcher workspace, matched by its current title or a legacy one."""
    out = cmux("list-workspaces")
    for title in (WORKSPACE_TITLE,) + LEGACY_WORKSPACE_TITLES:
        ws = workspace_titled(title, out)
        if ws:
            return ws
    return None


def update_pill(entries, workspace=None):
    count = sum(1 for e in entries if e["state"] in READY_STATES)
    ws = workspace or reviews_workspace()
    if not ws:
        return count
    if count:
        cmux("set-status", PILL_KEY, f"{count} ready", "--icon", PILL_ICON,
             "--color", PILL_COLOR, "--workspace", ws)
    else:
        cmux("clear-status", PILL_KEY, "--workspace", ws)
    return count


def click_command(workspace):
    cmd = "open -a cmux"
    binary = cmux_bin()
    if workspace and binary:
        cmd += f" && {shlex.quote(binary)} select-workspace --workspace {shlex.quote(workspace)}"
    return cmd


def banner(title, message, workspace, actionable=False, pr=None):
    """actionable: alerter banner with Options actions; open targets pr (None = oldest ready)."""
    if actionable and alerter_bin():
        args = [sys.executable, BANNER_ACTIONS, "--title", title, "--message", message]
        if pr is not None:
            args += ["--pr", str(pr)]
        try:
            # alerter blocks until the user acts. Own session: launchd kills the
            # job's process group when the tick exits.
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return
        except OSError:
            pass
    try:
        subprocess.run([notify_bin(), "--title", title, "--message", message,
                        "--group", GROUP, "--execute", click_command(workspace)],
                       capture_output=True, text=True, timeout=NOTIFY_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        pass


# --- dedup state -------------------------------------------------------------


def load_alerted():
    try:
        with open(alerts_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return {"ready": list(data.get("ready", [])), "failed": list(data.get("failed", []))}
    except (OSError, ValueError):
        return {"ready": [], "failed": []}


def save_alerted(state):
    os.makedirs(config.STATE_DIR, exist_ok=True)
    tmp = alerts_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, alerts_path())


# --- the alerts step ---------------------------------------------------------


def _label(e):
    return f"PR #{e['pr']}"


def run(now=None):
    now = now or events.parse_ts(events.now_iso())
    entries = events.reduce_events(events.read(), now)
    alerted = load_alerted()
    ws = reviews_workspace()

    new_ready = [e for e in entries
                 if e["state"] in READY_STATES and e["request_id"] not in alerted["ready"]]
    if len(new_ready) == 1:
        e = new_ready[0]
        banner("Review ready: run review-open", f"{_label(e)} review ready: {e.get('title') or e['repo']}", ws,
               actionable=True, pr=e["pr"])
    elif new_ready:
        banner("Reviews ready: run review-open", f"{len(new_ready)} reviews ready", ws,
               actionable=True)
    alerted["ready"] += [e["request_id"] for e in new_ready]

    # Reminder: once per request, only for ones that were not just announced.
    just = {e["request_id"] for e in new_ready}
    for e in entries:
        if e["state"] != "ready" or e["request_id"] in just or not e.get("ready_at"):
            continue
        waited = office_minutes(events.parse_ts(e["ready_at"]), now)
        if waited < config.REMINDER_AFTER_OFFICE_MINUTES:
            continue
        events.append("reminded", e["repo"], e["pr"], e["request_id"])
        banner("Review reminder: run review-open",
               f"{_label(e)} review still waiting ({waited} office min): "
               f"{e.get('title') or e['repo']}", ws, actionable=True, pr=e["pr"])
        e["state"] = "reminded"

    for e in entries:
        if e["state"] == "failed" and e["request_id"] not in alerted["failed"]:
            banner("Review failed",
                   f"Review failed: {_label(e)} ({e.get('failed_reason') or 'unknown'})", ws)
            alerted["failed"].append(e["request_id"])

    update_pill(entries, ws)
    save_alerted(alerted)


def main(argv=None):
    p = argparse.ArgumentParser(description="Review watcher alerts")
    p.add_argument("cmd", choices=("pill", "run"))
    args = p.parse_args(argv)
    if args.cmd == "pill":
        update_pill(events.reduce_events(events.read()))
    else:
        run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
