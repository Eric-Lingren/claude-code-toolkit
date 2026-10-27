#!/usr/bin/env python3
"""cases_import.py <agent> — one-time import of a legacy hand-built case set into the cases.jsonl format.

For agents whose evals/<agent>/cases.jsonl is still in the legacy shape (artifact-grounding-judge,
persona-accuracy). Moves the legacy file to cases.legacy.jsonl (build_cases.py and run-eval.mjs in that
folder read that name) and writes cases.jsonl with one "imported" case per legacy case:
  {"id", "agent", "kind": "imported", "tags", "input": {every legacy field except id/tags/expected},
   "expected": <legacy expected, untouched>, "origin": "legacy"}
Fixtures stay where the legacy tooling keeps them (fixtures/<name>, pinned by fixtures.lock.json).
Refuses if cases.legacy.jsonl already exists (import runs once).
"""
import os
import sys

import cases_lib as C


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    agent = sys.argv[1]
    legacy = os.path.join(C.agent_dir(agent), "cases.legacy.jsonl")
    if os.path.exists(legacy):
        sys.exit(f"refusing: {legacy} exists, already imported")
    cur = C.cases_path(agent)
    if not os.path.exists(cur):
        sys.exit(f"no legacy cases at {cur}")
    rows = C.load_cases(agent)
    if any("kind" in r and "input" in r for r in rows):
        sys.exit("refusing: cases.jsonl is already in the new format")
    out = []
    for r in rows:
        if "expected" not in r:
            sys.exit(f"legacy case {r.get('id')} has no expected answer")
        out.append({"id": r["id"], "agent": agent, "kind": "imported", "origin": "legacy",
                    "tags": r.get("tags", []),
                    "input": {k: v for k, v in r.items() if k not in ("id", "tags", "expected")},
                    "expected": r["expected"]})
    os.replace(cur, legacy)
    C.write_cases(agent, out)
    print(f"imported {len(out)} cases for {agent}; legacy file kept at {legacy}")


if __name__ == "__main__":
    main()
