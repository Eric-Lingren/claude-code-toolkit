#!/usr/bin/env python3
"""tag-attribution.py — causal tag for a new learning.

Reads a learning entry as JSON on stdin and prints one tag as JSON on stdout:

    {"kind": "regression_of",  "ref": "<change_id>"}
    {"kind": "recurrence_of",  "ref": "<learning id>"}
    {"kind": "new_defect",     "ref": null}

Rules, checked in this order:

  regression_of   the learning's escape point (path + section) sits inside a
                  changed section of a change record for the learning's skill
                  (improves) merged within the last 60 days. If several match,
                  the newest change wins. A null section or null improves can
                  never get this tag.
                  The window runs back from logging time, because a learning
                  has no observed time. A problem seen before a merge and
                  logged after it can still blame that change. log-learning.py
                  verifies the tag against the diff, which narrows this.
  recurrence_of   an already-applied learning (status "applied") has the same
                  cause (and cause_label when cause is "other") and the same
                  escape point. Until Phase 2 introduces clusters, that
                  learning's id stands in as the cluster id.
  new_defect      everything else: different path or section, no escape point,
                  or no change record.

Matching is on normalized values: a leading "claude-code-shared/" is stripped
from paths, and leading "#" marks and whitespace are stripped from section
headings, so "skills/debug/SKILL.md" + "Instrument" matches the change record's
"claude-code-shared/skills/debug/SKILL.md" + "## Instrument".

Usage:
    echo '{...learning...}' | python tag-attribution.py \\
        [--changes-dir DIR] [--learnings-file FILE] [--window-days N] [--now ISO]

Defaults: --changes-dir <repo>/attribution/change-records,
          --learnings-file <toolkit>/learnings/unified-learnings.jsonl,
          --window-days 60. A missing directory or file means no records.
Unreadable or malformed change/learning records are skipped.

Exit 0 on success. Exit 1 when stdin is not a JSON object.

Also importable (via importlib): tag_attribution(learning, changes, applied, now),
load_changes(dir), load_applied(file).
"""

import argparse
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

SHARED = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SHARED / "scripts" / "shared"))

from change_records_path import change_records_path  # noqa: E402

DEFAULT_CHANGES_DIR = change_records_path()
DEFAULT_LEARNINGS_FILE = SHARED / "learnings" / "unified-learnings.jsonl"
WINDOW_DAYS = 60
PATH_PREFIX = "claude-code-shared/"


def _norm_path(path):
    if not isinstance(path, str):
        return None
    path = path.strip()
    if path.startswith(PATH_PREFIX):
        path = path[len(PATH_PREFIX):]
    return path or None


def _norm_section(section):
    if not isinstance(section, str):
        return None
    return section.lstrip("#").strip() or None


def _escape_point(learning):
    """(normalized path, normalized section or None), or None without a usable escape point."""
    ep = learning.get("escape_point")
    if not isinstance(ep, dict):
        return None
    path = _norm_path(ep.get("path"))
    if path is None:
        return None
    return path, _norm_section(ep.get("section"))


def _parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_for_skill(change, improves):
    """True when the change touched a unit named like the learning's `improves` slug."""
    for unit in change.get("touched_units") or []:
        if isinstance(unit, str) and unit.split(":", 1)[-1] == improves:
            return True
    return False


def _fix_ids(learning, applied):
    """Ids of applied learnings with the same escape point and cause."""
    ep = _escape_point(learning)
    if ep is None:
        return set()
    return {
        row["id"]
        for row in applied or []
        if row.get("status") == "applied"
        and row.get("id")
        and _escape_point(row) == ep
        and _same_cause(learning, row)
    }


def _regression_ref(learning, changes, now, window_days, applied=()):
    ep = _escape_point(learning)
    improves = learning.get("improves")
    if ep is None or ep[1] is None or not improves:
        return None
    path, section = ep
    cutoff = now - timedelta(days=window_days)
    fix_ids = _fix_ids(learning, applied)
    best = None
    for change in changes:
        # A change that applied an earlier learning's fix is a recurrence, not a regression.
        refs = {r for r in change.get("source_refs") or [] if isinstance(r, str)}
        if fix_ids & refs:
            continue
        merged = _parse_time(change.get("merged_at"))
        if merged is None or merged < cutoff or merged > now:
            continue
        if not _is_for_skill(change, improves):
            continue
        hit = any(
            isinstance(s, dict)
            and _norm_path(s.get("path")) == path
            and _norm_section(s.get("section")) == section
            for s in change.get("sections") or []
        )
        if hit and (best is None or merged > best[0]):
            best = (merged, change.get("change_id"))
    return best[1] if best and best[1] else None


def _same_cause(a, b):
    if a.get("cause") != b.get("cause"):
        return False
    return a.get("cause") != "other" or a.get("cause_label") == b.get("cause_label")


def _recurrence_ref(learning, applied):
    ep = _escape_point(learning)
    if ep is None:
        return None
    for row in applied:
        if row.get("status") != "applied" or not row.get("id"):
            continue
        if _escape_point(row) == ep and _same_cause(learning, row):
            return row["id"]
    return None


def tag_attribution(learning, changes, applied, now=None, window_days=WINDOW_DAYS):
    """Return the {kind, ref} tag for `learning`."""
    now = now or datetime.now(timezone.utc)
    ref = _regression_ref(learning, changes, now, window_days, applied)
    if ref:
        return {"kind": "regression_of", "ref": ref}
    ref = _recurrence_ref(learning, applied)
    if ref:
        return {"kind": "recurrence_of", "ref": ref}
    return {"kind": "new_defect", "ref": None}


def load_changes(changes_dir):
    """Change records under changes_dir; unreadable files are skipped."""
    out = []
    d = pathlib.Path(changes_dir)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def load_applied(learnings_file):
    """Learning rows with status "applied"; malformed lines are skipped."""
    out = []
    p = pathlib.Path(learnings_file)
    if not p.is_file():
        return out
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if '"applied"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("status") == "applied":
            out.append(row)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--changes-dir", default=str(DEFAULT_CHANGES_DIR))
    parser.add_argument("--learnings-file", default=str(DEFAULT_LEARNINGS_FILE))
    parser.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    parser.add_argument("--now", default=None, help="ISO 8601 time to use instead of the clock (tests)")
    args = parser.parse_args()

    try:
        learning = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(f"ERROR: stdin is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(learning, dict):
        print("ERROR: input must be a JSON object", file=sys.stderr)
        sys.exit(1)

    now = _parse_time(args.now) if args.now else None
    tag = tag_attribution(
        learning,
        load_changes(args.changes_dir),
        load_applied(args.learnings_file),
        now,
        args.window_days,
    )
    print(json.dumps(tag))


if __name__ == "__main__":
    main()
