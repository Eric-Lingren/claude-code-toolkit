#!/usr/bin/env python3
"""cases_freeze.py <agent> [count] — select recent, varied harvested spawns and freeze them as real cases.

Reads .claude/agent-bench/<agent>/history (run harvest.py first). Picks 4 to 6 spawns (count, default 6,
clamped to 4..6): most recent first, then greedily preferring a new verdict, a new project and a new
session so the set is varied. For each pick it freezes the spawn prompt and every file the prompt
referenced under <evals>/<agent>/fixtures/<case_id>/ (gitignored) and records sha256 hashes in
cases.lock.json. A referenced file that no longer exists is rebuilt from, in order: the spawn's own Read
result, a Write in the parent session log, a whole-file Read in the parent session log. If it cannot be
rebuilt the spawn is skipped and the reason is logged (stdout + cases.lock.json "skipped").

Writes real cases to cases.jsonl (existing lines are kept; re-running replaces earlier real cases and
drops planted cases built on them; imported cases stay). Each case carries the answer the agent actually gave as its
expected answer (expected_source "recorded-output"). Prints whether the agent makes judgment calls
(then run the planted-edit step).
"""
import os
import re
import sys

import bench_lib as L
import cases_lib as C

HOME = os.path.expanduser("~")
PATH_RE = re.compile(r"(?<![\w:/.])(~)?(/[\w@.+~-]+(?:/[\w@.+~-]+)+)")
ROOTS = ("/Users/", "/home/", "/tmp/", "/private/", "/var/", "/opt/")
MAX_BYTES = 1_000_000
CANDIDATES = 24


def referenced_paths(prompt):
    out = []
    for m in PATH_RE.finditer(prompt or ""):
        p = m.group(2).rstrip(".,:;)'\"`")
        if m.group(1):
            p = HOME + p
        if not p.startswith(ROOTS) or "/.cch/" in p or "/.cco/" in p:
            continue
        if not os.path.splitext(p)[1]:  # needs an extension: a file, not a dir or a URL path
            continue
        if p not in out:
            out.append(p)
    return out


def strip_line_numbers(text):
    """Undo the Read tool's cat -n formatting."""
    rows = []
    for ln in text.split("\n"):
        m = re.match(r"^\s*\d+\t(.*)$", ln)
        if not m:
            return None
        rows.append(m.group(1))
    return "\n".join(rows) + "\n"


def tool_events(path):
    """Yield (tool_use dict, result text) for every tool call in a session jsonl."""
    import json
    uses, results = [], {}
    if not os.path.exists(path):
        return []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        c = (d.get("message") or {}).get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if b.get("type") == "tool_use":
                uses.append(b)
            elif b.get("type") == "tool_result":
                results[b.get("tool_use_id")] = b.get("content")
    return [(u, results.get(u.get("id"))) for u in uses]


def flat(c):
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c if isinstance(c, str) else ""


def rebuild_own_read(path, rec):
    for u, r in reversed(tool_events(rec["source_path"])):
        i = u.get("input") or {}
        if u.get("name") == "Read" and i.get("file_path") == path and not i.get("offset") and not i.get("limit"):
            t = strip_line_numbers(flat(r))
            if t is not None and flat(r).strip():
                return t
    return None


def rebuild(path, rec):
    """Return (content, how) or (None, reason)."""
    own = tool_events(rec["source_path"])
    sess_dir = os.path.dirname(os.path.dirname(rec["source_path"]))
    parent = sess_dir + ".jsonl"
    par = tool_events(parent)

    def whole_reads(events):
        for u, r in reversed(events):
            i = u.get("input") or {}
            if u.get("name") == "Read" and i.get("file_path") == path and not i.get("offset") and not i.get("limit"):
                t = strip_line_numbers(flat(r))
                if t is not None and flat(r).strip():
                    return t
        return None

    t = whole_reads(own)
    if t is not None:
        return t, "rebuilt:subagent-read"
    for u, _ in reversed(par):
        i = u.get("input") or {}
        if u.get("name") == "Write" and i.get("file_path") == path and isinstance(i.get("content"), str):
            return i["content"], "rebuilt:parent-write"
    t = whole_reads(par)
    if t is not None:
        return t, "rebuilt:parent-read"
    return None, "not on disk and not recoverable from the subagent or parent session log"


def pick(recs, n):
    recs = sorted(recs, key=lambda r: r.get("timestamp") or "", reverse=True)[:CANDIDATES]
    order, seen = [], {"v": set(), "p": set(), "s": set()}

    def feats(r):
        ans = C.answer_of(r.get("final_output")) or {}
        proj = r["source_path"].split("/projects/")[-1].split("/")[0]
        return str(ans.get("verdict", ans.get("status", "?"))), proj, r.get("session_id")

    pool = list(recs)
    while pool and len(order) < len(recs):
        def score(r):
            v, p, s = feats(r)
            return (2 * (v not in seen["v"]) + 2 * (p not in seen["p"]) + (s not in seen["s"]), -pool.index(r))
        best = max(pool, key=score)
        pool.remove(best)
        v, p, s = feats(best)
        seen["v"].add(v), seen["p"].add(p), seen["s"].add(s)
        order.append(best)
    return order  # preference order; caller takes the first n that freeze cleanly


def freeze_one(agent, rec):
    cid = "real-" + rec["agent_id"][:10]
    paths = referenced_paths(rec["spawn_prompt"])
    files, omitted = [], []
    stage = os.path.join(C.fixtures_dir(agent), cid)
    if os.path.exists(stage):
        import shutil
        shutil.rmtree(stage)
    os.makedirs(stage)
    for p in paths:
        rel = os.path.join("files", p.lstrip("/"))
        dest = os.path.join(stage, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isfile(dest):
            continue
        seen = rebuild_own_read(p, rec)
        if seen is not None:  # what the spawn actually saw beats the file as it is on disk today
            with open(dest, "w", encoding="utf-8") as dst:
                dst.write(seen)
            how = "as-seen:subagent-read"
        elif os.path.isfile(p):
            if os.path.getsize(p) > MAX_BYTES:
                omitted.append({"path": p, "reason": "larger than 1MB"})
                continue
            with open(p, "rb") as src, open(dest, "wb") as dst:
                dst.write(src.read())
            how = "copied"
        elif os.path.isdir(p):
            continue
        else:
            content, how = rebuild(p, rec)
            if content is None:
                import shutil
                shutil.rmtree(stage)
                return None, f"{p}: {how}"
            with open(dest, "w", encoding="utf-8") as dst:
                dst.write(content)
        files.append({"path": p, "fixture": rel, "sha256": L.sha256_file(dest), "how": how})
    with open(os.path.join(stage, "prompt.txt"), "w", encoding="utf-8") as fh:
        fh.write(rec["spawn_prompt"])
    expected = C.answer_of(rec.get("final_output"))
    if expected is None:
        import shutil
        shutil.rmtree(stage)
        return None, "recorded output is not a JSON object, so no expected answer"
    case = {"id": cid, "agent": agent, "kind": "real",
            "source": {"agent_id": rec["agent_id"], "session_id": rec.get("session_id"), "timestamp": rec.get("timestamp")},
            "input": {"prompt_file": f"fixtures/{cid}/prompt.txt", "files": files, "omitted": omitted},
            "expected": expected, "expected_source": "recorded-output"}
    return case, None


def is_judgment(agent, recs):
    contract = L.read_json(os.path.join(C.agent_dir(agent), "contract.json"))
    if contract and any(c.get("type") == "verdict_equals" for c in contract.get("checks", [])):
        return True
    with_verdict = sum(1 for r in recs if "verdict" in (C.answer_of(r.get("final_output")) or {}))
    return bool(recs) and with_verdict * 2 >= len(recs)


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    agent = sys.argv[1]
    n = max(4, min(6, int(sys.argv[2]))) if len(sys.argv) == 3 else 6
    recs = L.load_history(agent)
    if not recs:
        sys.exit(f"no history for {agent}: run harvest.py {agent} first")
    existing = C.load_cases(agent)
    # Re-running replaces earlier real cases and the planted cases built on them; imported cases stay.
    old_real = {c["id"] for c in existing if c["kind"] in ("real", "planted")}
    kept = [c for c in existing if c["kind"] == "imported"]
    frozen, skipped = [], []
    for rec in pick(recs, n):
        if len(frozen) >= n:
            break
        case, why = freeze_one(agent, rec)
        if case:
            frozen.append(case)
        else:
            skipped.append({"agent_id": rec["agent_id"], "reason": why})
            print(f"skipped {rec['agent_id']}: {why}")
    if len(frozen) < 4:
        sys.exit(f"only {len(frozen)} spawns could be frozen (need 4); {len(skipped)} skipped")
    lock = C.load_lock(agent)
    for cid in old_real - {c["id"] for c in frozen}:
        lock["cases"].pop(cid, None)
    lock["skipped"] = skipped
    C.L.write_json(C.lock_path(agent), lock)
    for c in frozen:
        C.lock_case(agent, c["id"])
    C.write_cases(agent, kept + frozen)
    rebuilt = sum(1 for c in frozen for f in c["input"]["files"] if f["how"].startswith("rebuilt"))
    print(f"froze {len(frozen)} real cases ({rebuilt} files rebuilt, {len(skipped)} spawns skipped) -> {C.cases_path(agent)}")
    print(f"judgment_agent: {'yes' if is_judgment(agent, recs) else 'no'}")
    for c in frozen:
        print(f"  {c['id']}  {c['source']['timestamp']}  expected={c['expected']}  files={len(c['input']['files'])}")


if __name__ == "__main__":
    main()
