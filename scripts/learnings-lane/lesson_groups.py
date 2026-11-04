#!/usr/bin/env python3
# lesson_groups.py: lesson-grouping route for null-target and candidate learnings
# What: route(rows, filed_ids, create_issue) groups the rows the lane never applies (improves null/empty, or
#   confidence "candidate") by normalized lesson. A group of GROUP_THRESHOLD or more becomes one idea issue
#   (system-generated, with evidence) via the injectable create_issue(payload). Smaller groups stay
#   unapplied, are re-read every kick, and are counted in the digest. Group id = hash of the normalized
#   lesson, so a group already filed is never filed again, even after a later matching learning joins.
# Never: edits a skill, sets or changes improves, calls a model. Rows are read, never mutated.
# When: each lane kick, beside the confirmed-target route (lane_select.py). Filed group ids persist in a state file.
# CLI: lesson_groups.py [--file PATH] [--state PATH] [--dry-run]  (real filing uses gh; tests inject create_issue)
import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("lane_config", HERE / "config.py")
config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(config)

UNIFIED = HERE.parent.parent / "learnings" / "unified-learnings.jsonl"
STATE = HERE.parent.parent / "learnings" / "lesson-groups-filed.json"
LABELS = ["idea", "system-generated"]


def is_routed_here(row):
    """Null-target or candidate-confidence learnings. Confirmed targeted rows go to lane clustering."""
    if row.get("status") not in (None, "captured"):
        return False
    return not row.get("improves") or row.get("confidence") == "candidate"


def norm_lesson(row):
    return re.sub(r"\s+", " ", str(row.get("lesson") or "")).strip().lower()


def group_id(lesson):
    return "lg-" + hashlib.sha1(lesson.encode()).hexdigest()[:12]


def group_rows(rows):
    groups = {}
    for r in rows:
        if not is_routed_here(r):
            continue
        key = norm_lesson(r)
        if key:
            groups.setdefault(key, []).append(r)
    return groups


def build_payload(gid, rows):
    lesson = next((r["lesson"] for r in rows if r.get("lesson")), "")
    evidence = [{"learning_id": r.get("id"), "evidence": r.get("evidence") or []} for r in rows]
    body = [f"System-generated from {len(rows)} learnings sharing one lesson.", "",
            f"Lesson: {lesson}", "", "Learnings:"]
    body += [f"- {r.get('id')}: {r.get('problem', '')}" for r in rows]
    return {"group_id": gid, "title": f"Idea: {lesson[:80]}", "body": "\n".join(body),
            "labels": list(LABELS), "system_generated": True,
            "learning_ids": [r.get("id") for r in rows], "evidence": evidence}


def route(rows, filed_ids, create_issue):
    """Returns {"filed": [payload...], "pending": n_learnings_in_small_groups, "pending_groups": n,
    "errors": [str]}. filed_ids is a set, updated in place with newly filed group ids. A create_issue
    failure is recorded in errors (group not marked filed, so it retries next pass) and the rest carry on."""
    filed, pending, pending_groups, errors = [], 0, 0, []
    for key, items in sorted(group_rows(rows).items()):
        gid = group_id(key)
        if gid in filed_ids:
            continue
        if len(items) >= config.GROUP_THRESHOLD:
            payload = build_payload(gid, items)
            try:
                create_issue(payload)
            except Exception as e:  # noqa: BLE001 - one failed group must not abort the rest
                detail = (getattr(e, "stderr", None) or "").strip() or str(e)
                errors.append(f"lesson issue {gid}: {detail}")
                continue
            filed_ids.add(gid)
            filed.append(payload)
        else:
            pending += len(items)
            pending_groups += 1
    return {"filed": filed, "pending": pending, "pending_groups": pending_groups, "errors": errors}


def gh_create_issue(payload, cwd=None):
    subprocess.run(["gh", "issue", "create", "--title", payload["title"], "--body", payload["body"],
                    *[a for l in payload["labels"] for a in ("--label", l)]],
                   capture_output=True, text=True, timeout=60, check=True, cwd=cwd)


def load_state(path):
    p = Path(path)
    return set(json.loads(p.read_text())) if p.exists() else set()


def save_state(path, ids):
    Path(path).write_text(json.dumps(sorted(ids), indent=2) + "\n")


def load_rows(path):
    rows = []
    for line in Path(path).read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="Group null-target and candidate learnings by lesson and file idea issues")
    ap.add_argument("--file", default=str(UNIFIED))
    ap.add_argument("--state", default=str(STATE))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    ids = load_state(a.state)
    res = route(load_rows(a.file), ids, (lambda p: None) if a.dry_run else gh_create_issue)
    if not a.dry_run:
        save_state(a.state, ids)
    print(json.dumps({"filed": [p["group_id"] for p in res["filed"]], "pending": res["pending"],
                      "pending_groups": res["pending_groups"], "errors": res["errors"]}))
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
