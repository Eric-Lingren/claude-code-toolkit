#!/usr/bin/env python3
"""Append-only JSONL event log and queue-state reducer for the review watcher.

Shared substrate: the poller, worker, watcher tick and alerts all write events
here and read queue state from here. Paths and limits come from config.py.

Runs are keyed per review-request event (request_id), never by head commit: two
requests on the same PR and the same head sha are two separate queue entries.

Event line: {"ts", "type", "repo", "pr", "request_id", "head_sha"?, "data": {}}

CLI
  events.py append TYPE --repo R --pr N [--request-id ID] [--head-sha SHA]
                        [--ts ISO] [--tool NAME] [--reason TEXT] [--data JSON]
      Appends one event and prints the request id. request_seen without
      --request-id gets a generated id; a request_seen whose id already exists is
      a no-op ("exists"), so pollers can re-report the same request safely.
      Other event types without --request-id attach to the latest request for
      that PR (exit 1 if there is none).
  events.py state [--repo R] [--pr N] [--state S] [--now ISO] [--json] [--write]
      Reduces the log to one entry per request: queued, running, ready,
      reminded, approved, plus terminal posted/failed/dismissed (dismissed: a
      ready review retired by `reviews.sh rerun`, no failure banner). wait_office_minutes runs
      from the request to now, or to the terminal event for posted/failed.
  events.py log-path     print the event log path from config.
"""
import argparse
import fcntl
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import config
from office_minutes import office_minutes

EVENT_TYPES = (
    "request_seen", "review_started", "review_ready", "reminded",
    "approved", "posted", "failed", "dismissed", "tool_denied",
    # System event, no queue effect: written with --repo _system --pr 0
    # --request-id cmux-probe. The reducer skips it (no request_seen for that id).
    "cmux_probe",
)
STATES = ("queued", "running", "ready", "reminded", "approved", "posted", "failed",
          "dismissed")
TERMINAL = ("posted", "failed", "dismissed")
# event type -> queue state it moves a request into
_TRANSITIONS = {
    "request_seen": "queued",
    "review_started": "running",
    "review_ready": "ready",
    "reminded": "reminded",
    "approved": "approved",
    "posted": "posted",
    "failed": "failed",
    "dismissed": "dismissed",
}
_TIMESTAMP_FIELD = {
    "review_started": "started_at", "review_ready": "ready_at",
    "reminded": "reminded_at", "approved": "approved_at",
    "posted": "posted_at", "failed": "failed_at", "dismissed": "dismissed_at",
}


def parse_ts(value):
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.astimezone()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- log ---------------------------------------------------------------------


def read(path=None):
    """All events in file order. Missing log reads as empty."""
    path = path or config.EVENT_LOG_PATH
    try:
        fh = open(path, encoding="utf-8")
    except FileNotFoundError:
        return []
    with fh:
        return [json.loads(line) for line in fh if line.strip()]


def append(etype, repo, pr, request_id=None, head_sha=None, ts=None, data=None, path=None):
    """Append one event. Returns (event, created).

    request_seen with an existing request_id is not written (created=False).
    Other types without request_id attach to the latest request for the PR;
    ValueError if the PR has none.
    """
    if etype not in EVENT_TYPES:
        raise ValueError(f"unknown event type {etype!r}")
    path = path or config.EVENT_LOG_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        existing = [json.loads(l) for l in fh if l.strip()]
        seen = [e for e in existing if e["type"] == "request_seen"
                and e["repo"] == repo and e["pr"] == pr]
        if etype == "request_seen":
            request_id = request_id or uuid.uuid4().hex[:12]
            for e in seen:
                if e["request_id"] == request_id:
                    return e, False
        elif not request_id:
            if not seen:
                raise ValueError(f"no request for {repo}#{pr}")
            request_id = seen[-1]["request_id"]
        event = {"ts": ts or now_iso(), "type": etype, "repo": repo, "pr": pr,
                 "request_id": request_id}
        if head_sha:
            event["head_sha"] = head_sha
        event["data"] = data or {}
        fh.seek(0, os.SEEK_END)
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")
        fh.flush()
        return event, True


# --- reducer -----------------------------------------------------------------


def reduce_events(events, now=None):
    """Derive one queue entry per request_id. `now` is an aware datetime."""
    now = now or datetime.now(timezone.utc)
    entries = {}
    for ev in events:
        rid = ev["request_id"]
        if ev["type"] == "request_seen":
            if rid in entries:
                continue
            entry = {
                "request_id": rid, "repo": ev["repo"], "pr": ev["pr"],
                "head_sha": ev.get("head_sha"), "state": "queued",
                "requested_at": ev["ts"], "denied_tool_calls": 0,
                "failed_reason": None, "_end": None,
            }
            entry.update(ev.get("data") or {})
            entries[rid] = entry
            continue
        entry = entries.get(rid)
        if entry is None:
            continue
        if ev["type"] == "tool_denied":
            entry["denied_tool_calls"] += 1
            continue
        if entry["state"] in TERMINAL:
            continue
        entry["state"] = _TRANSITIONS[ev["type"]]
        entry[_TIMESTAMP_FIELD[ev["type"]]] = ev["ts"]
        if ev["type"] == "failed":
            entry["failed_reason"] = (ev.get("data") or {}).get("reason")
        if entry["state"] in TERMINAL:
            entry["_end"] = ev["ts"]
    out = []
    for entry in entries.values():
        end = parse_ts(entry.pop("_end")) if entry["_end"] else now
        entry.pop("_end", None)
        entry["wait_office_minutes"] = office_minutes(parse_ts(entry["requested_at"]), end)
        out.append(entry)
    return out


# --- CLI ---------------------------------------------------------------------


def _format_table(entries):
    if not entries:
        return "(no requests)"
    return "\n".join(
        f"{e['repo']}#{e['pr']}  {e['request_id']}  {e['state']:<8}  wait {e['wait_office_minutes']}m"
        for e in entries
    )


def _build_parser():
    p = argparse.ArgumentParser(description="Review watcher event log and queue state")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("append", help="append one event")
    a.add_argument("type", choices=EVENT_TYPES)
    a.add_argument("--repo", required=True)
    a.add_argument("--pr", required=True, type=int)
    a.add_argument("--request-id")
    a.add_argument("--head-sha")
    a.add_argument("--ts", help="ISO timestamp (default: now)")
    a.add_argument("--tool", help="denied tool name (tool_denied)")
    a.add_argument("--reason", help="failure reason (failed)")
    a.add_argument("--data", help="extra JSON object stored on the event")

    s = sub.add_parser("state", help="reduce the log to queue state")
    s.add_argument("--repo")
    s.add_argument("--pr", type=int)
    s.add_argument("--state", choices=STATES)
    s.add_argument("--now", help="ISO timestamp to measure waits against")
    s.add_argument("--json", action="store_true")
    s.add_argument("--write", action="store_true", help="also write the snapshot to the state path")

    sub.add_parser("log-path", help="print the event log path")
    return p


def _cmd_append(args, parser):
    data = {}
    if args.data:
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError as exc:
            parser.error(f"--data is not valid JSON: {exc}")
        if not isinstance(data, dict):
            parser.error("--data must be a JSON object")
    if args.tool:
        data["tool"] = args.tool
    if args.reason:
        data["reason"] = args.reason
    try:
        event, created = append(args.type, args.repo, args.pr, args.request_id,
                                args.head_sha, args.ts, data)
    except ValueError as exc:
        print(f"events.py: {exc}", file=sys.stderr)
        return 1
    print(event["request_id"] if created else f"exists {event['request_id']}")
    return 0


def _cmd_state(args):
    now = parse_ts(args.now) if args.now else None
    entries = reduce_events(read(), now)
    entries = [e for e in entries
               if (not args.repo or e["repo"] == args.repo)
               and (args.pr is None or e["pr"] == args.pr)
               and (not args.state or e["state"] == args.state)]
    if args.write:
        os.makedirs(os.path.dirname(config.STATE_PATH), exist_ok=True)
        tmp = config.STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2)
        os.replace(tmp, config.STATE_PATH)
    print(json.dumps(entries, indent=2) if args.json else _format_table(entries))
    return 0


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "append":
        return _cmd_append(args, parser)
    if args.cmd == "state":
        return _cmd_state(args)
    print(config.EVENT_LOG_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
