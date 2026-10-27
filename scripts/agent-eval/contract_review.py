#!/usr/bin/env python3
"""contract_review.py <agent> — validate the draft contract and print the approval screen.

Reads <bench>/<agent>/draft.contract.json (written by the Opus drafter):
  {"agent": "...",
   "checks": [{"id","type",...params,"source":{"tag":"role|caller|side_effect","file","line","quote"}}],
   "conflicts": [{"id","description","options":[{"label","check":{...full check with source...}}]}]}

1. Drops (and records in draft["dropped"]) any check with no real citation: bad tag, missing file,
   line out of range, or quote not found at the cited lines. Also drops checks of an unknown type
   unless the type is written "proposed:<name>" (a new shared grade.py type needing user approval).
2. Runs grade.py's engine over the harvested history (zero tokens).
3. Prints one block per check: source tag, file:line, historical pass rate, 1-2 real failing examples,
   then source conflicts, proposed new types and dropped checks.
The draft file is rewritten with the dropped list. Nothing is frozen here (see contract_freeze.py).
"""
import sys

import bench_lib as L


def rate_str(s):
    return "n/a (no applicable records)" if s["rate"] is None else f"{100 * s['rate']:.1f}% ({s['pass']}/{s['pass'] + s['fail']}, {s['na']} n/a)"


def clean(draft):
    """Validate draft in place. Returns list of newly dropped entries."""
    dropped = draft.setdefault("dropped", [])
    kept = []
    for c in draft.get("checks", []):
        why = L.validate_source(c.get("source"))
        t = c.get("type", "")
        if not why and not (t in L.BUILTIN_TYPES or t.startswith("proposed:")):
            why = f"unknown check type {t!r}; use a built-in or 'proposed:<name>'"
        if why:
            dropped.append({"id": c.get("id"), "reason": why})
        else:
            kept.append(c)
    draft["checks"] = kept
    for cf in draft.get("conflicts", []):
        good = []
        for o in cf.get("options", []):
            why = L.validate_source((o.get("check") or {}).get("source"))
            if why:
                dropped.append({"id": f"conflict {cf.get('id')} option '{o.get('label', '')}'", "reason": why})
            else:
                good.append(o)
        cf["options"] = good
    draft["conflicts"] = [cf for cf in draft.get("conflicts", []) if len(cf["options"]) >= 2]
    return dropped


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    agent = sys.argv[1]
    p = L.draft_path(agent)
    draft = L.read_json(p)
    if draft is None:
        sys.exit(f"no draft at {p}")
    clean(draft)
    L.write_json(p, draft)
    recs = L.load_history(agent)
    gradable = {"checks": [c for c in draft["checks"] if not c["type"].startswith("proposed:")]}
    stats = {s["id"]: s for s in L.grade_contract(gradable, recs)}

    print(f"APPROVAL SCREEN  agent: {agent}  history records: {len(recs)}  checks: {len(draft['checks'])}")
    print("=" * 78)
    for i, c in enumerate(draft["checks"], 1):
        s = c["source"]
        print(f"[{i}] {c['id']}  ({c['type']})")
        print(f"    source: {s['tag']}  {s['file']}:{s['line']}")
        print(f"    cites:  {L.norm(s['quote'])[:200]}")
        st = stats.get(c["id"])
        if st is None:
            print("    history: NOT GRADED (proposed new check type, needs approval)")
            continue
        print(f"    history pass rate: {rate_str(st)}")
        for f in st["fails"][:2]:
            print(f"    failing example {f['agent_id']} ({f['timestamp']}): {f['detail']}")
            print(f"        output: {f['output_head']}")
    conflicts = draft.get("conflicts", [])
    if conflicts:
        print("-" * 78 + "\nSOURCE DISAGREEMENTS (pick a winner for each; run contract_resolve.py <agent> <id> <n>)")
        for cf in conflicts:
            print(f"  {cf['id']}: {cf.get('description', '')}")
            for n, o in enumerate(cf["options"], 1):
                s = o["check"]["source"]
                print(f"    {n}. {o.get('label', '')}  [{s['tag']} {s['file']}:{s['line']}]")
    props = [c for c in draft["checks"] if c["type"].startswith("proposed:")]
    if props:
        print("-" * 78 + "\nPROPOSED NEW CHECK TYPES (need explicit approval; implemented in shared grade.py, never in an agent folder)")
        for c in props:
            print(f"  {c['type']} for check {c['id']}: {c.get('proposal', '(no rationale given)')}")
    if draft.get("dropped"):
        print("-" * 78 + "\nDROPPED (no citable source)")
        for d in draft["dropped"]:
            print(f"  {d['id']}: {d['reason']}")
    print("=" * 78)
    blockers = len(conflicts) + len(props)
    print("freeze blocked by: " + (f"{len(conflicts)} conflict(s), {len(props)} proposed type(s)" if blockers else "nothing; ready for approval"))


if __name__ == "__main__":
    main()
