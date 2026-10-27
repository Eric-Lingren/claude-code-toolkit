#!/usr/bin/env python3
"""contract_resolve.py <agent> <conflict-id> <option-number> — apply the user's pick for a source conflict.

Replaces (by id) or appends the chosen option's check in the draft's checks and removes the conflict.
"""
import sys

import bench_lib as L


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    agent, cid, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
    p = L.draft_path(agent)
    draft = L.read_json(p)
    if draft is None:
        sys.exit(f"no draft at {p}")
    cf = next((c for c in draft.get("conflicts", []) if c["id"] == cid), None)
    if not cf:
        sys.exit(f"no conflict {cid}")
    if not 1 <= n <= len(cf["options"]):
        sys.exit(f"option must be 1..{len(cf['options'])}")
    chosen = cf["options"][n - 1]["check"]
    draft["checks"] = [c for c in draft["checks"] if c["id"] != chosen["id"]] + [chosen]
    draft["conflicts"] = [c for c in draft["conflicts"] if c["id"] != cid]
    L.write_json(p, draft)
    print(f"resolved {cid}: option {n} ({cf['options'][n - 1].get('label', '')})")


if __name__ == "__main__":
    main()
