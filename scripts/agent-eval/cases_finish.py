#!/usr/bin/env python3
"""cases_finish.py verify|advance <agent> — check the case set, then move the queue to stage iterate.

verify   re-hash every frozen fixture against cases.lock.json (missing, changed or extra files fail),
         check every case has a non-empty expected answer, every planted case's base exists, and every
         planted edit still applies (find present once in its base, replace present once in the case).
         Imported cases are checked for an expected answer only (their fixtures belong to legacy tooling).
advance  runs verify, then advances the queue entry from cases to iterate. Never edits queue.json by hand.
"""
import os
import sys

import bench_lib as L
import cases_lib as C


def verify(agent):
    cases = C.load_cases(agent)
    lock = C.load_lock(agent)
    errs, ids = [], {c["id"] for c in cases}
    if not cases:
        errs.append("cases.jsonl is empty or missing")
    for c in cases:
        if not c.get("expected"):
            errs.append(f"{c['id']}: no expected answer")
        if c["kind"] == "imported":
            continue
        want = lock["cases"].get(c["id"])
        if want is None:
            errs.append(f"{c['id']}: not in cases.lock.json")
            continue
        root = os.path.join(C.fixtures_dir(agent), c["id"])
        if not os.path.isdir(root):
            errs.append(f"{c['id']}: fixture dir missing")
            continue
        have = C.hash_tree(root)
        for rel in sorted(set(want) | set(have)):
            if want.get(rel) != have.get(rel):
                errs.append(f"{c['id']}/{rel}: hash {'missing' if rel not in have else 'extra' if rel not in want else 'changed'}")
        if c["kind"] == "planted":
            if c.get("base") not in ids:
                errs.append(f"{c['id']}: base {c.get('base')} missing")
                continue
            e = c["edit"]
            base_t = open(os.path.join(C.fixtures_dir(agent), c["base"], e["file"]), encoding="utf-8").read()
            case_t = open(os.path.join(root, e["file"]), encoding="utf-8").read()
            if base_t.count(e["find"]) != 1:
                errs.append(f"{c['id']}: find no longer matches exactly once in base")
            if base_t.replace(e["find"], e["replace"], 1) != case_t:
                errs.append(f"{c['id']}: fixture is not base + edit")
    kinds = {}
    for c in cases:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    return errs, kinds


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("verify", "advance"):
        sys.exit(__doc__)
    cmd, agent = sys.argv[1:]
    errs, kinds = verify(agent)
    for e in errs:
        print("FAIL " + e)
    if errs:
        sys.exit(f"{len(errs)} problem(s); not advancing")
    print(f"verified {sum(kinds.values())} cases {kinds}; all hashes match cases.lock.json")
    if cmd == "advance":
        if not kinds.get("real"):
            sys.exit("refusing: no frozen real cases")
        os.system(f"python3 '{os.path.join(L.HERE, 'bench_queue.py')}' ensure '{agent}' >/dev/null")
        os.system(f"python3 '{os.path.join(L.HERE, 'bench_queue.py')}' advance '{agent}' iterate")


if __name__ == "__main__":
    main()
