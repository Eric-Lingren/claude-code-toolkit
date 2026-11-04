#!/usr/bin/env python3
"""verdict_apply.py: apply a headless run file's stale, invalid and retarget verdicts to the learnings file.

What: reads a run file (run-file-contract.json, checked by validate_run_file.py) and, through
  update-learning.py, sets matching learnings to status stale or invalid (reason stored), or retargets
  them (--improves, --improves-type, --status captured: the existing retarget form).
  applied / needs-grill / draft-failed verdicts are ignored.
  Returns {"dropped": [{"id","verdict","reason"}], "retargeted": [{"id","improves","improves_type","reason"}],
  "skipped": [ids already in the target state], "unknown": [ids not in the learnings file], "errors": [str]}.
  "dropped" is what the PR body lists. An unknown id is reported and never aborts the pass.
  Idempotent: a row already at the verdict's end state is skipped, so a second run changes nothing.
When: same pass as lane_writeback.py, on the main checkout only, never from a lane worktree.
Env: LOG_LEARNING_DEST overrides the learnings dir (tests).
CLI: verdict_apply.py RUN_FILE [--learnings-dir DIR]
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPDATE_LEARNING = HERE.parent / "learning" / "update-learning.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


lane_select = _load("lane_select")
validate_run_file = _load("validate_run_file")

APPLICABLE = ("stale", "invalid", "retarget")


def _update(args, env):
    return subprocess.run([sys.executable, str(UPDATE_LEARNING), *args], capture_output=True,
                          text=True, env=env, check=False)


def apply_verdicts(run_file, learnings_dir=None):
    if learnings_dir is None:
        learnings_dir = os.environ.get("LOG_LEARNING_DEST") or str(lane_select.UNIFIED.parent)
    env = dict(os.environ, LOG_LEARNING_DEST=str(learnings_dir))
    res = {"dropped": [], "retargeted": [], "skipped": [], "unknown": [], "errors": []}
    doc = json.loads(Path(run_file).read_text())
    problems = validate_run_file.errors(doc)
    if problems:
        res["errors"] = [f"run file invalid: {p}" for p in problems]
        return res
    rows = {r["id"]: r for r in lane_select.load_rows(Path(learnings_dir) / "unified-learnings.jsonl")
            if r.get("id")}
    for v in doc["verdicts"]:
        kind, lid, reason = v["verdict"], v["learning_id"], v["reason"]
        if kind not in APPLICABLE:
            continue
        row = rows.get(lid)
        if row is None:
            res["unknown"].append(lid)
            continue
        slug = typ = None
        if kind == "retarget":
            slug, typ = v.get("suggested_improves"), v.get("suggested_improves_type")
            if not slug or not typ:
                res["errors"].append(f"{lid}: retarget verdict missing suggested_improves/type")
                continue
            if (row.get("improves"), row.get("improves_type"), row.get("status")) == (slug, typ, "captured"):
                res["skipped"].append(lid)
                continue
            args = ["--id", lid, "--improves", slug, "--improves-type", typ, "--status", "captured"]
        else:
            if row.get("status") == kind:
                res["skipped"].append(lid)
                continue
            args = ["--id", lid, "--status", kind, "--reason", reason]
        proc = _update(args, env)
        if proc.returncode != 0:
            res["errors"].append(f"{lid}: {proc.stderr.strip()}")
            continue
        if kind == "retarget":
            rows[lid] = dict(row, improves=slug, improves_type=typ, status="captured")
            res["retargeted"].append({"id": lid, "improves": slug, "improves_type": typ, "reason": reason})
        else:
            rows[lid] = dict(row, status=kind)
            res["dropped"].append({"id": lid, "verdict": kind, "reason": reason})
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_file")
    ap.add_argument("--learnings-dir", default=None)
    args = ap.parse_args(argv)
    res = apply_verdicts(args.run_file, args.learnings_dir)
    print(json.dumps(res))
    for e in res["errors"]:
        print(f"warning: {e}", file=sys.stderr)
    for u in res["unknown"]:
        print(f"warning: unknown learning id {u}", file=sys.stderr)
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
