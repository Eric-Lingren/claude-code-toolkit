#!/usr/bin/env python3
"""One tick of the PR review watcher (launchd runs it every CHECK_INTERVAL_MINUTES).

A tick, in order:
  1. Office-hours gate: outside ACTIVE_WINDOW on WEEKDAYS (TIMEZONE) exit silently,
     unless a manual command opened an after-hours window (manual_window.py).
  2. Poll GitHub (poll.py) so new review requests become request_seen events.
  3. Reconcile: a request in ready/reminded/approved state whose PR now carries a
     review by the viewer submitted after the request gets a `posted` event, so the
     queue clears even when Eric posts outside the watcher.
  4. Supervise runs: a run past the timeout is killed (process group) and gets a
     `failed` event (reason "timeout"); a run whose process died without a terminal
     event gets `failed` too.
  5. Start queued requests, oldest first, until MAX_CONCURRENT_REVIEWS are alive.
     Each run gets a worktree of the PR head and run-review.sh is launched there,
     detached. For a same-repo PR it is the `wt` worktree of the PR's head branch
     (WORKTREE_ENGINE <branch> --no-install --no-sync, so review-open can later
     run `wt <branch>` for the split panes). It is used only if its HEAD is the PR
     head commit; a worktree that already existed is never moved, a new one is
     fast-forwarded. Otherwise (fork PR, branch gone, diverged) it is a detached
     worktree under RUNS_DIR/<request_id>/wt.
  6. Remove the detached run worktree of every request that reached posted or
     failed. `wt` worktrees are left for the user (`wt rm`). A ready review keeps
     its worktree: review-open resumes the session from that cwd.
  7. Alerts (alerts.py): pill count, one batched banner for newly ready reviews,
     the 60 office-minute reminder, failure banners. Never fails the tick.

Usage: watcher.sh [--once] [--dry-run]
  --once     accepted for manual runs; a tick is always a single pass
  --dry-run  poll read-only and print what would happen; change nothing

Run bookkeeping: RUNS_DIR/<request_id>/run.json holds {pid, started_epoch}; the
child is its own session leader so its pid is also its process group id.

Test hooks (env): REVIEW_WATCHER_NOW (ISO) replaces the clock for the window gate;
REVIEW_WATCHER_TIMEOUT_SECONDS replaces REVIEW_TIMEOUT_MINUTES; REVIEW_WATCHER_GH
swaps gh; REVIEW_WATCHER_HOME and REVIEW_WATCHER_CHECKOUTS relocate state/clones;
REVIEW_WATCHER_WORKTREE_ENGINE swaps the `wt` engine (WORKTREE_BASE moves its output).
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

import alerts
import workspace
import config
import events
import manual_window
from office_minutes import in_active_window

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_REVIEW = os.path.join(HERE, "run-review.sh")
POLL = os.path.join(HERE, "poll.py")


def log(msg):
    print(msg, flush=True)


def after_state_change(changes):
    """Hook point: called once per tick with the list of state changes it made.

    Each change is {"kind": started|failed|posted, "repo", "pr", "request_id",
    "reason"?}. Alerts are wired in here by a later task; it does nothing now.
    """


# --- paths and process bookkeeping -------------------------------------------


def safe_id(request_id):
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", request_id).lstrip(".")
    return cleaned or "_"


def run_dir(request_id):
    return os.path.join(config.RUNS_DIR, safe_id(request_id))


def worktree_dir(request_id):
    return os.path.join(run_dir(request_id), "wt")


def read_run(request_id):
    try:
        with open(os.path.join(run_dir(request_id), "run.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie child of this process still answers signal 0; reap it if ours.
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    return True


def kill_group(pid):
    for sig, wait in ((signal.SIGTERM, 3.0), (signal.SIGKILL, 3.0)):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.time() + wait
        while time.time() < deadline:
            if not pid_alive(pid):
                return
            time.sleep(0.1)


def timeout_seconds():
    override = os.environ.get("REVIEW_WATCHER_TIMEOUT_SECONDS")
    return float(override) if override else config.REVIEW_TIMEOUT_MINUTES * 60


def now():
    override = os.environ.get("REVIEW_WATCHER_NOW")
    return events.parse_ts(override) if override else datetime.now(timezone.utc)


# --- git worktrees -----------------------------------------------------------


def git(checkout, *args):
    return subprocess.run(["git", "-C", checkout, *args], capture_output=True, text=True)


def remove_worktree(entry):
    wt = worktree_dir(entry["request_id"])
    if not os.path.isdir(wt):
        return False
    checkout = config.REPO_CHECKOUTS.get(entry["repo"])
    if checkout and os.path.isdir(checkout):
        git(checkout, "worktree", "remove", "--force", wt)
    if os.path.isdir(wt):
        shutil.rmtree(wt, ignore_errors=True)
        if checkout and os.path.isdir(checkout):
            git(checkout, "worktree", "prune")
    return True


def pr_head_branch(entry):
    """Head branch of a same-repo PR, or None (fork PR, gh error, no branch name)."""
    proc = subprocess.run([gh_bin(), "pr", "view", str(entry["pr"]), "--repo", entry["repo"],
                           "--json", "headRefName,isCrossRepository"],
                          capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout) if proc.returncode == 0 else {}
    except ValueError:
        return None
    if data.get("isCrossRepository"):
        return None
    return data.get("headRefName") or None


def branch_worktree(checkout, branch):
    """Path of the worktree that has branch checked out, or None."""
    listing = git(checkout, "worktree", "list", "--porcelain").stdout
    path = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def prepare_branch_worktree(entry, checkout, sha):
    """`wt` worktree of the PR head branch at sha, or None (caller falls back)."""
    branch = pr_head_branch(entry)
    if not branch or not os.path.exists(config.WORKTREE_ENGINE):
        return None
    existed = branch_worktree(checkout, branch)
    # "n" answers the engine's "Create from base?" prompt: a branch that is not on
    # origin must never be invented from main.
    try:
        made = subprocess.run([config.WORKTREE_ENGINE, branch, "--no-install", "--no-sync"],
                              cwd=checkout, input="n\n", capture_output=True, text=True,
                              timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = made.stdout.strip().splitlines()
    path = lines[-1] if made.returncode == 0 and lines else ""
    if not os.path.isdir(path):
        return None
    head = git(path, "rev-parse", "HEAD").stdout.strip()
    if head == sha:
        return path
    if existed:  # someone's worktree at another commit: never move it
        log(f"wt worktree {path} is at {head[:12]}, not PR head {sha[:12]}: using a detached one")
        return None
    if (git(path, "merge-base", "--is-ancestor", head, sha).returncode == 0
            and git(path, "merge", "--ff-only", sha).returncode == 0):
        return path
    log(f"wt worktree {path} cannot fast-forward to {sha[:12]}: using a detached one")
    return None


def prepare_worktree(entry):
    """Fetch the PR head; prefer the branch's `wt` worktree, else a detached one.
    Returns (path, error)."""
    checkout = config.REPO_CHECKOUTS.get(entry["repo"])
    if not checkout or not os.path.isdir(checkout):
        return None, f"no local checkout for {entry['repo']}"
    fetched = git(checkout, "fetch", "origin", f"pull/{entry['pr']}/head")
    if fetched.returncode != 0:
        return None, f"git fetch failed: {fetched.stderr.strip()[:300]}"
    sha = git(checkout, "rev-parse", "FETCH_HEAD")
    if sha.returncode != 0:
        return None, f"git rev-parse failed: {sha.stderr.strip()[:300]}"
    os.makedirs(run_dir(entry["request_id"]), exist_ok=True)  # runner.log, run.json
    branch_wt = prepare_branch_worktree(entry, checkout, sha.stdout.strip())
    if branch_wt:
        return branch_wt, None
    wt = worktree_dir(entry["request_id"])
    if os.path.isdir(wt):  # leftover of an earlier attempt
        remove_worktree(entry)
    added = git(checkout, "worktree", "add", "--detach", wt, sha.stdout.strip())
    if added.returncode != 0:
        return None, f"git worktree add failed: {added.stderr.strip()[:300]}"
    return wt, None


# --- GitHub (read-only) ------------------------------------------------------


def gh_bin():
    return os.environ.get("REVIEW_WATCHER_GH", "gh")


def gh_json(*args):
    proc = subprocess.run([gh_bin(), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gh {args[0]} failed")
    return proc.stdout


def viewer_review_after(entry, login):
    out = gh_json("pr", "view", str(entry["pr"]), "--repo", entry["repo"], "--json", "reviews")
    since = events.parse_ts(entry["requested_at"])
    for rev in json.loads(out).get("reviews") or []:
        if (rev.get("author") or {}).get("login") != login or not rev.get("submittedAt"):
            continue
        if events.parse_ts(rev["submittedAt"]) > since:
            return True
    return False


# --- tick steps --------------------------------------------------------------


def poll(dry_run):
    cmd = [sys.executable, POLL, "--json"] + (["--dry-run"] if dry_run else [])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"watcher: poll failed: {proc.stderr.strip()}")
        return []
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return []


def entries_now():
    return events.reduce_events(events.read())


def append(etype, entry, **kw):
    events.append(etype, entry["repo"], entry["pr"], entry["request_id"], **kw)


def reconcile(entries, dry_run, changes):
    pending = [e for e in entries if e["state"] in ("ready", "reminded", "approved")]
    if not pending:
        return
    try:
        login = gh_json("api", "user", "-q", ".login").strip()
    except RuntimeError as exc:
        log(f"watcher: viewer lookup failed: {exc}")
        return
    for e in pending:
        try:
            posted = viewer_review_after(e, login)
        except (RuntimeError, ValueError) as exc:
            log(f"watcher: review lookup failed for {e['repo']}#{e['pr']}: {exc}")
            continue
        if not posted:
            continue
        if dry_run:
            log(f"would mark posted {e['repo']}#{e['pr']} {e['request_id']}")
            continue
        append("posted", e, data={"reconciled": True})
        log(f"posted (reconciled) {e['repo']}#{e['pr']} {e['request_id']}")
        changes.append({"kind": "posted", "repo": e["repo"], "pr": e["pr"],
                        "request_id": e["request_id"]})


def supervise(alive_before, dry_run, changes):
    """Kill timed-out runs; fail runs that died silently. Returns request ids freed."""
    freed = set()
    limit = timeout_seconds()
    for e in entries_now():
        rid = e["request_id"]
        run = read_run(rid)
        if e["state"] != "running" or not run:
            continue
        if alive_before.get(rid):
            if time.time() - run["started_epoch"] <= limit:
                continue
            if dry_run:
                log(f"would time out {e['repo']}#{e['pr']} {rid}")
                continue
            kill_group(run["pid"])
            reason = "timeout"
        else:
            if dry_run:
                log(f"would fail dead run {e['repo']}#{e['pr']} {rid}")
                continue
            reason = "worker exited without a result"
        append("failed", e, data={"reason": reason})
        log(f"failed ({reason}) {e['repo']}#{e['pr']} {rid}")
        changes.append({"kind": "failed", "repo": e["repo"], "pr": e["pr"],
                        "request_id": rid, "reason": reason})
        freed.add(rid)
    return freed


def launch(entry, changes):
    rid = entry["request_id"]
    wt, err = prepare_worktree(entry)
    if err:
        append("failed", entry, data={"reason": err})
        log(f"failed ({err}) {entry['repo']}#{entry['pr']} {rid}")
        changes.append({"kind": "failed", "repo": entry["repo"], "pr": entry["pr"],
                        "request_id": rid, "reason": err})
        return False
    rdir = run_dir(rid)
    with open(os.path.join(rdir, "runner.log"), "ab") as out:
        proc = subprocess.Popen(
            ["bash", RUN_REVIEW, "--repo", entry["repo"], "--pr", str(entry["pr"]),
             "--run-dir", rdir, "--request-id", rid],
            cwd=wt, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
            start_new_session=True,
        )
    with open(os.path.join(rdir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump({"pid": proc.pid, "started_epoch": time.time()}, fh)
    log(f"started {entry['repo']}#{entry['pr']} {rid} pid {proc.pid}")
    changes.append({"kind": "started", "repo": entry["repo"], "pr": entry["pr"],
                    "request_id": rid})
    return True


def tick(dry_run):
    at = now()
    if not (in_active_window(at) or manual_window.is_open(at)):
        return 0
    new_requests = poll(dry_run)

    # Liveness first: a run seen dead here has already written its own events.
    alive = {}
    for e in entries_now():
        run = read_run(e["request_id"])
        alive[e["request_id"]] = bool(run) and pid_alive(run["pid"])

    changes = []
    entries = entries_now()
    reconcile(entries, dry_run, changes)
    freed = supervise(alive, dry_run, changes)

    entries = entries_now()
    active = sum(1 for e in entries
                 if alive.get(e["request_id"]) and e["request_id"] not in freed
                 and e["state"] not in events.TERMINAL)
    slots = max(config.MAX_CONCURRENT_REVIEWS - active, 0)
    queued = sorted((e for e in entries if e["state"] == "queued"
                     and not alive.get(e["request_id"])),
                    key=lambda e: e["requested_at"])
    if dry_run:
        queued += [{"repo": r["repo"], "pr": r["pr"], "request_id": r["request_id"]}
                   for r in new_requests]
        for e in queued[:slots]:
            log(f"would start {e['repo']}#{e['pr']} {e['request_id']}")
        for e in queued[slots:]:
            log(f"would wait {e['repo']}#{e['pr']} {e['request_id']} (at capacity)")
        if not queued:
            log("nothing to start")
        return 0
    for e in queued[:slots]:
        launch(e, changes)

    for e in entries_now():
        if e["state"] in events.TERMINAL and remove_worktree(e):
            log(f"removed worktree {e['request_id']}")

    os.makedirs(os.path.dirname(config.STATE_PATH), exist_ok=True)
    tmp = config.STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(entries_now(), fh, indent=2)
    os.replace(tmp, config.STATE_PATH)
    if changes:
        after_state_change(changes)
    try:
        workspace.ensure()
    except Exception as exc:  # the workspace must never fail the tick
        log(f"watcher: ensure workspace failed: {exc}")
    try:
        alerts.run(at)
    except Exception as exc:  # alerts must never fail the tick
        log(f"watcher: alerts failed: {exc}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--once", action="store_true", help="run a single tick (the default)")
    p.add_argument("--dry-run", action="store_true", help="print what would happen")
    args = p.parse_args(argv)
    return tick(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
