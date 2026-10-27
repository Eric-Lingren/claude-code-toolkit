#!/usr/bin/env python3
"""child_lookup.py <session_id> <parent_agent_id> <parent_agent> <out.json> — find the real recorded
outputs of child agents spawned inside one recorded session, for the live runner's child shim.

Scans <home>/.cch|.cco/projects/*/<session_id>/subagents/agent-*.meta.json. Children whose spawn prompt
equals a prompt the parent recorded in its own Agent calls (history/<parent_agent_id>.json) rank first.
Writes [{agent_type, agent_id, prompt, output, timestamp}] to out.json. Zero tokens."""
import glob
import os
import sys

import bench_lib as L
import harvest


def main():
    if len(sys.argv) != 5:
        sys.exit("usage: child_lookup.py <session_id> <parent_agent_id> <parent_agent> <out.json>")
    session, parent_id, parent_agent, out = sys.argv[1:]
    parent = L.read_json(os.path.join(L.bench_dir(), parent_agent, "history", parent_id + ".json"), {})
    wanted = {c["input"].get("prompt") for c in parent.get("tool_calls", [])
              if c.get("name") in ("Agent", "Task") and isinstance(c.get("input"), dict)}
    home = os.path.expanduser("~")
    kids = []
    for cfg in (".cch", ".cco"):
        for mp in glob.glob(os.path.join(home, cfg, "projects", "*", session, "subagents", "agent-*.meta.json")):
            meta = L.read_json(mp, {})
            jp = mp[: -len(".meta.json")] + ".jsonl"
            if not meta.get("agentType") or not os.path.exists(jp):
                continue
            rec = harvest.parse(jp, meta)
            aid = os.path.basename(jp)[len("agent-"):-len(".jsonl")]
            if aid == parent_id:
                continue
            kids.append({"agent_type": meta["agentType"], "agent_id": aid, "prompt": rec["spawn_prompt"],
                         "output": rec["final_output"], "timestamp": rec["timestamp"] or ""})
    kids.sort(key=lambda k: (k["prompt"] not in wanted, k["timestamp"]))
    L.write_json(out, kids)
    print(f"{len(kids)} recorded children in session {session} ({sum(k['prompt'] in wanted for k in kids)} matched the parent's spawn prompts)")


if __name__ == "__main__":
    main()
