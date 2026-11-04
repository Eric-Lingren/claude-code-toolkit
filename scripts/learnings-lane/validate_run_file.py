#!/usr/bin/env python3
"""Validate a headless run file against run-file-contract.json.

Usage: validate_run_file.py RUN_FILE      (RUN_FILE '-' reads stdin)
       validate_run_file.py --sample      (validates the contract's own sample)
Exit 0 when valid; prints one error per line and exits 1 otherwise.
"""
import json
import re
import sys
from pathlib import Path

CONTRACT = Path(__file__).resolve().parent / "run-file-contract.json"
VERDICTS = {"applied", "stale", "invalid", "retarget", "needs-grill", "draft-failed"}
STATUSES = {"committed", "needs-grill", "no-change"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
TYPES = {"skill", "agent", "process", "contract"}


def _str(o, k):
    return isinstance(o.get(k), str) and o[k].strip() != ""


def errors(doc):
    errs = []
    if not isinstance(doc, dict):
        return ["run file is not an object"]
    if doc.get("version") != "1.0":
        errs.append("version must be '1.0'")
    if not _str(doc, "target"):
        errs.append("target missing or empty")
    cs, vs = doc.get("clusters"), doc.get("verdicts")
    if not isinstance(cs, list):
        errs.append("clusters missing or not a list")
        cs = []
    if not isinstance(vs, list):
        errs.append("verdicts missing or not a list")
        vs = []
    cluster_ids = set()
    for n, c in enumerate(cs):
        if not isinstance(c, dict):
            errs.append(f"clusters[{n}] not an object")
            continue
        if not _str(c, "id"):
            errs.append(f"clusters[{n}].id missing or empty")
        cluster_ids.add(c.get("id"))
        if c.get("status") not in STATUSES:
            errs.append(f"clusters[{n}].status must be one of {sorted(STATUSES)}")
        lids = c.get("learning_ids")
        if not isinstance(lids, list) or not lids:
            errs.append(f"clusters[{n}].learning_ids missing or empty")
        if c.get("status") == "committed":
            if not _str(c, "commit"):
                errs.append(f"clusters[{n}].commit required when committed")
            if not isinstance(c.get("applied_ids"), list) or not c.get("applied_ids"):
                errs.append(f"clusters[{n}].applied_ids required when committed")
        elif "commit" in c:
            errs.append(f"clusters[{n}].commit only allowed when committed")
        if c.get("status") == "needs-grill" and not (isinstance(c.get("reasons"), list) and c["reasons"]):
            errs.append(f"clusters[{n}].reasons required when needs-grill")
    for n, v in enumerate(vs):
        if not isinstance(v, dict):
            errs.append(f"verdicts[{n}] not an object")
            continue
        for k in ("learning_id", "cluster_id", "reason"):
            if not _str(v, k):
                errs.append(f"verdicts[{n}].{k} missing or empty")
        if v.get("verdict") not in VERDICTS:
            errs.append(f"verdicts[{n}].verdict must be one of {sorted(VERDICTS)}")
        if v.get("cluster_id") not in cluster_ids:
            errs.append(f"verdicts[{n}].cluster_id not in clusters")
        if v.get("verdict") == "retarget":
            if not _str(v, "suggested_improves"):
                errs.append(f"verdicts[{n}].suggested_improves required for retarget")
            elif not SLUG_RE.match(v["suggested_improves"]) or ".." in v["suggested_improves"]:
                errs.append(f"verdicts[{n}].suggested_improves must match ^[a-z0-9][a-z0-9._-]*$ and not contain '..'")
            if v.get("suggested_improves_type") not in TYPES:
                errs.append(f"verdicts[{n}].suggested_improves_type must be one of {sorted(TYPES)}")
    return errs


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    if argv[1] == "--sample":
        doc = json.loads(CONTRACT.read_text())["sample"]
    else:
        doc = json.loads(sys.stdin.read() if argv[1] == "-" else Path(argv[1]).read_text())
    errs = errors(doc)
    for e in errs:
        print(e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
