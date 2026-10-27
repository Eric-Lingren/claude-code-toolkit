#!/usr/bin/env python3
"""harvest.py <agent> — harvest recorded subagent spawns into history records.

Scans <home>/.cch/projects and <home>/.cco/projects for
  <project>/<session>/subagents/agent-<id>.jsonl (+ .meta.json with agentType)
keeps spawns whose meta agentType equals <agent>, and writes one JSON record
per spawn to <repo>/.claude/agent-bench/<agent>/history/<agent_id>.json.
Zero tokens. The history dir is gitignored.

Record: agent, agent_id, session_id, config_dir, source_path, timestamp, model,
spawn_prompt, final_output, tool_calls[{id,name,input,result,is_error}],
files_written[], usage{input,output,cache_read,cache_creation}.
"""
import glob
import json
import os
import subprocess
import sys

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def result_text(c):
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c if isinstance(c, str) else json.dumps(c)


def parse(path, meta):
    prompt, ts, model, calls, results = None, None, meta.get("model"), [], {}
    msgs = {}  # message id -> {"text": str, "usage": dict}; one API message spans many lines
    order = []
    session = None
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        session = session or d.get("sessionId")
        t = d.get("type")
        m = d.get("message") or {}
        if t == "user" and not d.get("isMeta"):
            c = m.get("content")
            if isinstance(c, str):
                if prompt is None:
                    prompt, ts = c, d.get("timestamp")
            elif isinstance(c, list):
                for b in c:
                    if b.get("type") == "tool_result":
                        results[b.get("tool_use_id")] = (result_text(b.get("content")), bool(b.get("is_error")))
                    elif b.get("type") == "text" and prompt is None:
                        prompt, ts = b.get("text", ""), d.get("timestamp")
        elif t == "assistant":
            model = m.get("model") or model
            mid = m.get("id") or d.get("uuid")
            if mid not in msgs:
                msgs[mid] = {"text": "", "usage": {}}
                order.append(mid)
            for b in m.get("content") or []:
                if b.get("type") == "text":
                    msgs[mid]["text"] += b.get("text", "")
                elif b.get("type") == "tool_use":
                    calls.append({"id": b.get("id"), "name": b.get("name"), "input": b.get("input")})
            u = m.get("usage") or {}
            if u:
                msgs[mid]["usage"] = u  # later chunks carry the final output_tokens
    for c in calls:
        r = results.get(c["id"])
        c["result"], c["is_error"] = r if r else (None, False)
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    for mid in order:
        u = msgs[mid]["usage"]
        usage["input"] += u.get("input_tokens", 0) or 0
        usage["output"] += u.get("output_tokens", 0) or 0
        usage["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
        usage["cache_creation"] += u.get("cache_creation_input_tokens", 0) or 0
    final = ""
    for mid in reversed(order):
        if msgs[mid]["text"].strip():
            final = msgs[mid]["text"]
            break
    # The harness delivers the real result through SubagentHandback when present.
    for c in reversed(calls):
        if c["name"] == "SubagentHandback" and isinstance(c["input"], dict) and c["input"].get("message"):
            final = c["input"]["message"]
            break
    written = []
    for c in calls:
        if c["name"] in WRITE_TOOLS and isinstance(c["input"], dict):
            p = c["input"].get("file_path") or c["input"].get("notebook_path")
            if p and p not in written:
                written.append(p)
    return {"spawn_prompt": prompt, "final_output": final, "tool_calls": calls, "files_written": written,
            "usage": usage, "timestamp": ts, "model": model, "session_id": session}


def repo_root():
    return subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: harvest.py <agent-name>")
    agent = sys.argv[1]
    home = os.path.expanduser("~")
    out = os.path.join(repo_root(), ".claude", "agent-bench", agent, "history")
    os.makedirs(out, exist_ok=True)
    found = 0
    per_dir = {}
    for cfg in (".cch", ".cco"):
        pat = os.path.join(home, cfg, "projects", "*", "*", "subagents", "agent-*.meta.json")
        for mp in sorted(glob.glob(pat)):
            try:
                meta = json.load(open(mp))
            except ValueError:
                continue
            if meta.get("agentType") != agent:
                continue
            jp = mp[: -len(".meta.json")] + ".jsonl"
            if not os.path.exists(jp):
                continue
            rec = parse(jp, meta)
            if rec["spawn_prompt"] is None:
                continue
            aid = os.path.basename(jp)[len("agent-"):-len(".jsonl")]
            rec.update({"agent": agent, "agent_id": aid, "config_dir": cfg, "source_path": jp,
                        "session_id": rec["session_id"] or os.path.basename(os.path.dirname(os.path.dirname(jp)))})
            with open(os.path.join(out, aid + ".json"), "w") as fh:
                json.dump(rec, fh, indent=1)
            found += 1
            per_dir[cfg] = per_dir.get(cfg, 0) + 1
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(per_dir.items())) or "none"
    print(f"found {found} spawns of {agent} ({detail}) -> {out}")


if __name__ == "__main__":
    main()
