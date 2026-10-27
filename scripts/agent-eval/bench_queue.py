#!/usr/bin/env python3
"""bench_queue.py <cmd> [args] — manage .claude/agent-bench/queue.json.

  list                    print every agent (from agents/**/*.md) with its queue stage, tab separated
  ensure <agent>          add <agent> (and any other agent missing from the queue) at stage "contract";
                          print the agent's entry as JSON
  sync                    add every agent missing from the queue at stage "contract" and save
  get <agent>            print the entry as JSON (exit 1 if missing)
  advance <agent> <stage> set stage (contract|cases|iterate); stage can only move forward
"""
import json
import sys

import bench_lib as L


def sync(q):
    for a in L.agent_names():
        q["agents"].setdefault(a, {"stage": "contract", "updated": L.now()})
    return q


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    q = L.load_queue()
    if a[0] == "list":
        sync(q)
        for n, e in sorted(q["agents"].items()):
            print(f"{n}\t{e['stage']}")
    elif a[0] == "sync":
        before = set(q["agents"])
        sync(q)
        L.write_json(L.queue_path(), q)
        for n in sorted(set(q["agents"]) - before):
            print(f"added {n} at stage contract")
        print(f"{len(q['agents'])} agents in queue")
    elif a[0] == "ensure" and len(a) == 2:
        if a[1] not in L.agent_names():
            sys.exit(f"unknown agent: {a[1]} (no agents/**/{a[1]}.md)")
        sync(q)
        L.write_json(L.queue_path(), q)
        print(json.dumps({"agent": a[1], **q["agents"][a[1]]}))
    elif a[0] == "get" and len(a) == 2:
        e = q["agents"].get(a[1])
        if not e:
            sys.exit(1)
        print(json.dumps({"agent": a[1], **e}))
    elif a[0] == "advance" and len(a) == 3:
        if a[2] not in L.STAGES:
            sys.exit(f"stage must be one of {L.STAGES}")
        e = q["agents"].get(a[1])
        if not e:
            sys.exit(f"{a[1]} not in queue")
        if L.STAGES.index(a[2]) < L.STAGES.index(e["stage"]):
            sys.exit(f"cannot move {a[1]} back from {e['stage']} to {a[2]}")
        e["stage"], e["updated"] = a[2], L.now()
        L.write_json(L.queue_path(), q)
        print(f"{a[1]} -> {a[2]}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
