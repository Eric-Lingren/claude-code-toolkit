#!/usr/bin/env python3
"""rank.py [N] — sync the queue with agents/**/*.md, then print the top N (default 4) agents to improve.

Steps (zero tokens):
  1. bench_queue.sync: any agents/**/*.md missing from queue.json is added at stage "contract" (history empty).
  2. Scan recorded spawns (same sources as harvest.py) in memory and count spawns per agent. Only
     spawns made since the agent file's last commit count (bench_lib.current_history); older spawns
     ran a different prompt.
  3. A spawn FAILS when the agent has a frozen contract (evals/<agent>/contract.json) and any check
     fails (grade.check), else (no contract yet) when its final output is blank or its last tool call errored.
  4. score = failure_rate * spawn_count (= failed spawns). Highest first; agents with score 0
     (passing, or never spawned) are at the bottom, ordered by spawn count.

Agents flagged end_state in the queue (set by preflight.py) sort below every other agent while no
cited file was hand-edited.

Output, one tab-separated line per agent:  rank  agent  stage  failure_rate  spawns  failed  reason
"""
import glob
import json
import os
import sys

import bench_lib as L
import bench_queue
import harvest
import preflight


def contract_for(agent):
    return L.read_json(os.path.join(L.evals_dir(), agent, "contract.json"))


def failed(rec, contract):
    if contract:
        return any(L.grade.check(c, rec)[0] is False for c in contract["checks"])
    calls = rec["tool_calls"]
    return not (rec["final_output"] or "").strip() or bool(calls and calls[-1].get("is_error"))


def scan(agents):
    """-> {agent: [record, ...]} for tree agents only (records parsed in memory, nothing written)."""
    want, out = set(agents), {a: [] for a in agents}
    home = os.path.expanduser("~")
    for cfg in (".cch", ".cco"):
        for mp in sorted(glob.glob(os.path.join(home, cfg, "projects", "*", "*", "subagents", "agent-*.meta.json"))):
            try:
                meta = json.load(open(mp))
            except ValueError:
                continue
            a = meta.get("agentType")
            jp = mp[: -len(".meta.json")] + ".jsonl"
            if a not in want or not os.path.exists(jp):
                continue
            rec = harvest.parse(jp, meta)
            if rec["spawn_prompt"] is not None:
                out[a].append(rec)
    return out


def rank(agents):
    recs = scan(agents)
    ended = {a: e.get("end_state") for a, e in L.load_queue()["agents"].items()}
    rows = []
    for a in agents:
        c = contract_for(a)
        cur, _ = L.current_history(a, recs[a])
        n = len(cur)
        f = sum(failed(r, c) for r in cur)
        rate = f / n if n else 0.0
        basis = "contract checks" if c else "no contract yet: blank output or errored last tool call"
        if n == 0 and recs[a]:
            reason = f"no spawns since the agent file last changed ({len(recs[a])} older spawns ignored)"
        elif n == 0:
            reason = "never spawned in recorded history"
        elif f == 0:
            reason = f"passing: 0 of {n} spawns failed ({basis})"
        else:
            reason = f"{f} of {n} spawns failed ({basis}); score {rate:.3f} x {n} = {f}"
        rows.append({"agent": a, "rate": rate, "spawns": n, "failed": f, "reason": reason})
    for r in rows:
        r["ended"] = r["failed"] == 0 and bool(ended.get(r["agent"])) and preflight.still_ended(r["agent"])
        if r["ended"]:
            r["reason"] = "end state: all checks pass on planted cases and history"
    rows.sort(key=lambda r: (r["failed"] == 0, r["ended"], -r["failed"], -r["spawns"], r["agent"]))
    return rows


def main():
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    q = L.load_queue()
    bench_queue.sync(q)
    L.write_json(L.queue_path(), q)
    for i, r in enumerate(rank(sorted(q["agents"]))[:top], 1):
        stage = q["agents"][r["agent"]]["stage"]
        print(f"{i}\t{r['agent']}\t{stage}\t{r['rate']:.1%}\t{r['spawns']}\t{r['failed']}\t{r['reason']}")


if __name__ == "__main__":
    main()
