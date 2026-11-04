#!/usr/bin/env python3
"""Weekly drift and verified-regression alerts with a suspect list.

What: compares correction-rate windows per skill (correction-rate.py) and alerts
  on (a) drift: the rate rose by >= THRESHOLD (absolute) versus the window of
  equal width just before it, or (b) a verified regression: a learning from the
  target week whose attribution_tag is still regression_of <change_id> (an
  ungrounded tag was already downgraded to new_defect by log-learning.py, so a
  surviving tag is a verified one). Each alert names the skill, the before and
  after numbers and the suspect changes newest first, each with a PR link built
  from the change records (source_refs "pr:N" -> <target_repo>/pull/N, else the
  merge commit URL).
When: from the weekly usage job (weekly-usage-report.sh), which also puts the
  alert text in the digest. Alert text goes to stdout (nothing when quiet);
  delivery goes through scripts/notify/notify.sh.

Rules:
  - Alert-only. No git writes, no PRs, no revert, no bisect: this script never
    runs git or gh.
  - A drift alert needs the skill's window to be alarm-eligible (>= 10 runs,
    same gate as correction-rate.py) and the "before" window to hold >= 10 runs
    so the baseline is not noise. A verified regression has no run gate: a
    surviving regression_of tag was already checked against the real diff, so
    it alerts for a skill with any number of runs (before/after rates may then
    be n/a).
  - Suspects are changes whose touched_units name the skill, merged between the
    start of the before window and the end of the target week. For a verified
    regression the tagged change is always listed (marked), even outside that
    and even when the list is capped at MAX_SUSPECTS.

Usage:
  drift-alert.py [--week 2026-W45] [--report-dir DIR] [--learnings FILE]
                 [--changes-dir DIR] [--threshold 0.10] [--repo ORG/REPO]
                 [--json] [--no-notify]
  Default --week is the last completed ISO week.

Environment:
  DRIFT_ALERT_NOTIFY_CMD  command (may carry args) used for delivery instead of
                          scripts/notify/notify.sh. Tests point it at a stub.
"""
import argparse
import datetime
import glob
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.normpath(os.path.join(HERE, "..", ".."))
DEFAULT_LEARNINGS = os.path.join(SHARED, "learnings", "unified-learnings.jsonl")
sys.path.insert(0, os.path.join(SHARED, "scripts", "shared"))
from change_records_path import change_records_path  # noqa: E402

DEFAULT_CHANGES = str(change_records_path())
DEFAULT_NOTIFY = os.path.join(SHARED, "scripts", "notify", "notify.sh")
DEFAULT_REPO = "Eric-Lingren/dotfiles"

THRESHOLD = 0.10  # absolute rate rise (corrections per run) that counts as drift
MAX_SUSPECTS = 10


def _load_correction_rate():
    spec = importlib.util.spec_from_file_location(
        "correction_rate", os.path.join(HERE, "correction-rate.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CR = _load_correction_rate()


def parse_ts(value):
    """ISO timestamp (with Z or offset) -> aware UTC datetime; None if bad."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def week_start(week):
    y, w = week.split("-W")
    d = datetime.date.fromisocalendar(int(y), int(w), 1)
    return datetime.datetime(d.year, d.month, d.day,
                             tzinfo=datetime.timezone.utc)


def load_changes(path):
    """Change records in a directory; unreadable or malformed ones are skipped."""
    out = []
    for f in sorted(glob.glob(os.path.join(path, "chg-*.json"))):
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("change_id"):
            out.append(rec)
    return out


def change_link(rec, default_repo=DEFAULT_REPO):
    """(kind, url): PR link from source_refs 'pr:N', else the merge commit URL."""
    repo = rec.get("target_repo") or default_repo
    for ref in rec.get("source_refs") or []:
        if isinstance(ref, str) and ref.startswith("pr:") and ref[3:].isdigit():
            return "pr", f"https://github.com/{repo}/pull/{ref[3:]}"
    sha = rec.get("merged_sha")
    if sha:
        return "commit", f"https://github.com/{repo}/commit/{sha}"
    return None, None


def window_stats(learnings, run_counts, skill, weeks):
    n = sum(1 for r in learnings
            if r.get("improves") == skill and CR.iso_week(r.get("timestamp")) in weeks)
    runs = sum(run_counts.get(skill, {}).get(w, 0) for w in weeks)
    return {"learnings": n, "runs": runs, "rate": n / runs if runs else None,
            "weeks": list(weeks)}


def suspects_for(skill, changes, start, end, tagged_ref=None, default_repo=DEFAULT_REPO):
    """Changes touching the skill, newest first, with links."""
    found = []
    for rec in changes:
        merged = parse_ts(rec.get("merged_at"))
        is_tagged = tagged_ref is not None and rec.get("change_id") == tagged_ref
        units = [u.split(":", 1)[1] for u in rec.get("touched_units") or []
                 if isinstance(u, str) and ":" in u]
        touches = skill in units
        in_window = merged is not None and start <= merged < end
        if not (is_tagged or (touches and in_window)):
            continue
        kind, url = change_link(rec, default_repo)
        found.append((merged or datetime.datetime.min.replace(
            tzinfo=datetime.timezone.utc), {
                "change_id": rec["change_id"],
                "merged_at": rec.get("merged_at"),
                "link_kind": kind,
                "url": url,
                "tagged": is_tagged,
            }))
    found.sort(key=lambda p: p[0], reverse=True)
    kept = found[:MAX_SUSPECTS]
    # The tagged change is the evidence behind a verified regression: the cap
    # must never push it out. It takes the oldest slot; order stays newest first.
    if tagged_ref is not None and not any(s["tagged"] for _, s in kept):
        tagged = [p for p in found if p[1]["tagged"]]
        if tagged:
            kept = kept[:MAX_SUSPECTS - 1] + tagged[:1]
            kept.sort(key=lambda p: p[0], reverse=True)
    return [s for _, s in kept]


def _fmt_rate(stats):
    rate = "n/a" if stats["rate"] is None else f"{stats['rate']:.3f}"
    return f"{rate} ({stats['learnings']} learnings / {stats['runs']} runs)"


def _suspect_lines(suspects):
    if not suspects:
        return ["  suspect changes: none found in change records"]
    lines = ["  suspect changes (newest first):"]
    for i, s in enumerate(suspects, 1):
        day = (s["merged_at"] or "unknown date")[:10]
        mark = " [tagged by the learning]" if s["tagged"] else ""
        link = s["url"] or "no link"
        lines.append(f"    {i}. {s['change_id']} merged {day}{mark} {link}")
    return lines


def _render(alert):
    skill, before, after = alert["skill"], alert["before"], alert["after"]
    span = f"{', '.join(reversed(after['weeks']))}"
    if alert["kind"] == "drift":
        head = (f"DRIFT ALERT: {skill} correction rate rose "
                f"{before['rate']:.3f} -> {after['rate']:.3f} ({alert['week']})")
    else:
        head = (f"VERIFIED REGRESSION: {skill} learning {alert['learning_id']} is "
                f"tagged regression_of {alert['ref']} ({alert['week']})")
    lines = [head,
             f"  before: {_fmt_rate(before)} [{', '.join(reversed(before['weeks']))}]",
             f"  after:  {_fmt_rate(after)} [{span}]"]
    lines += _suspect_lines(alert["suspects"])
    return "\n".join(lines)


def detect(learnings, run_counts, changes, week, threshold=THRESHOLD,
           default_repo=DEFAULT_REPO):
    """List of alert dicts (drift first, then verified regressions)."""
    rates = CR.compute(learnings, run_counts, week)["skills"]
    end = week_start(week) + datetime.timedelta(days=7)
    alerts = []
    stats_cache = {}

    def stats_for(skill):
        if skill not in stats_cache:
            n = rates[skill]["window_weeks"]
            both = CR.weeks_back(week, 2 * n)
            stats_cache[skill] = (
                window_stats(learnings, run_counts, skill, both[:n]),
                window_stats(learnings, run_counts, skill, both[n:]),
                week_start(both[-1]))
        return stats_cache[skill]

    for skill in sorted(rates):
        if not rates[skill]["alarmable"]:
            continue
        after, before, start = stats_for(skill)
        if before["runs"] < CR.MIN_RUNS or before["rate"] is None:
            continue
        if after["rate"] - before["rate"] < threshold - 1e-9:
            continue
        alert = {"kind": "drift", "skill": skill, "week": week,
                 "before": before, "after": after,
                 "suspects": suspects_for(skill, changes, start, end,
                                          default_repo=default_repo)}
        alert["text"] = _render(alert)
        alerts.append(alert)

    for row in learnings:
        tag = row.get("attribution_tag") or {}
        skill = row.get("improves")
        if (tag.get("kind") != "regression_of" or not tag.get("ref")
                or not skill or CR.iso_week(row.get("timestamp")) != week
                or skill not in rates):
            continue
        # No alarmable gate here: a verified tag alerts however few runs the
        # skill has. Only the rate-drift alert needs the run-count gate.
        after, before, start = stats_for(skill)
        alert = {"kind": "verified_regression", "skill": skill, "week": week,
                 "learning_id": row.get("id"), "ref": tag["ref"],
                 "before": before, "after": after,
                 "suspects": suspects_for(skill, changes, start, end,
                                          tagged_ref=tag["ref"],
                                          default_repo=default_repo)}
        alert["text"] = _render(alert)
        alerts.append(alert)
    return alerts


def deliver(alerts, notify_cmd):
    """Send one notification summarising the alerts. Never raises."""
    title = f"Claude Code drift: {len(alerts)} alert(s)"
    message = "; ".join(a["text"].splitlines()[0] for a in alerts)
    try:
        cmd = shlex.split(notify_cmd) + ["--title", title, "--message", message,
                                         "--group", "drift-alert"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            print(f"drift-alert: notify exited {proc.returncode}: "
                  f"{proc.stderr.strip()}", file=sys.stderr)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        print(f"drift-alert: notify failed: {e}", file=sys.stderr)


def last_completed_week():
    d = datetime.date.today() - datetime.timedelta(days=7)
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=(
        "Alert on per-skill correction-rate drift or a verified regression_of "
        "tag, listing suspect changes newest first. Alert-only: no git writes, "
        "no PRs."))
    ap.add_argument("--week", default=last_completed_week())
    ap.add_argument("--report-dir", default=None,
                    help="dir holding snapshots-*.json (default: usage-reports)")
    ap.add_argument("--learnings", default=DEFAULT_LEARNINGS)
    ap.add_argument("--changes-dir", default=DEFAULT_CHANGES)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--repo", default=DEFAULT_REPO,
                    help="fallback ORG/REPO for links when a record has no target_repo")
    ap.add_argument("--json", action="store_true", help="print alerts as JSON")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args(argv)

    alerts = detect(CR.load_learnings(args.learnings),
                    CR.load_run_counts(args.report_dir),
                    load_changes(args.changes_dir), args.week,
                    threshold=args.threshold, default_repo=args.repo)
    if alerts:
        if args.json:
            json.dump(alerts, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write("\n\n".join(a["text"] for a in alerts) + "\n")
        if not args.no_notify:
            deliver(alerts, os.environ.get("DRIFT_ALERT_NOTIFY_CMD", DEFAULT_NOTIFY))
    elif args.json:
        sys.stdout.write("[]\n")


if __name__ == "__main__":
    main()
