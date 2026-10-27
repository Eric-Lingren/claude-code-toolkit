#!/usr/bin/env python3
"""contract_freeze.py <agent> — freeze the approved draft. Run ONLY after the user approves.

Refuses when: a conflict is unresolved, a proposed new check type remains, any citation is no longer
valid, or contract.json already exists. On success:
  <evals>/<agent>/contract.json   checks + sha256 fingerprint of every cited file
  <evals>/<agent>/baseline.json   historical pass rates (the free baseline), recomputed by the same
                                  engine as grade.py at freeze time
  queue.json                      agent advanced to stage "cases"
"""
import os
import sys

import bench_lib as L


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    agent = sys.argv[1]
    draft = L.read_json(L.draft_path(agent))
    if draft is None:
        sys.exit(f"no draft at {L.draft_path(agent)}")
    out_dir = os.path.join(L.evals_dir(), agent)
    cpath = os.path.join(out_dir, "contract.json")
    if os.path.exists(cpath):
        sys.exit(f"refusing: {cpath} already frozen")
    if draft.get("conflicts"):
        sys.exit(f"refusing: {len(draft['conflicts'])} unresolved source conflict(s)")
    checks = draft.get("checks", [])
    if not checks:
        sys.exit("refusing: no checks")
    ids = [c["id"] for c in checks]
    if len(set(ids)) != len(ids):
        sys.exit("refusing: duplicate check ids")
    for c in checks:
        if c["type"] not in L.BUILTIN_TYPES:
            sys.exit(f"refusing: check {c['id']} has non-built-in type {c['type']!r} (new types need approval and a grade.py change)")
        why = L.validate_source(c.get("source"))
        if why:
            sys.exit(f"refusing: check {c['id']}: {why}")
    fp = {}
    for c in checks:
        f = c["source"]["file"]
        fp[f] = L.sha256_file(L.cited_path(f))
    contract = {"agent": agent, "frozen_at": L.now(), "checks": checks, "fingerprints": dict(sorted(fp.items()))}
    recs = L.load_history(agent)
    stats = L.grade_contract(contract, recs)
    baseline = {"agent": agent, "graded_at": contract["frozen_at"], "records": len(recs),
                "checks": {s["id"]: {"pass": s["pass"], "fail": s["fail"], "na": s["na"], "rate": s["rate"]} for s in stats}}
    L.write_json(cpath, contract)
    L.write_json(os.path.join(out_dir, "baseline.json"), baseline)
    os.system(f"python3 '{os.path.join(L.HERE, 'bench_queue.py')}' ensure '{agent}' >/dev/null")
    os.system(f"python3 '{os.path.join(L.HERE, 'bench_queue.py')}' advance '{agent}' cases")
    print(f"frozen {len(checks)} checks, {len(fp)} fingerprints, {len(recs)} history records -> {out_dir}")


if __name__ == "__main__":
    main()
