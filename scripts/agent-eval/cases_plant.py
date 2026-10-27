#!/usr/bin/env python3
"""cases_plant.py <agent> [proposals.json] — turn proposed find/replace edits into planted cases.

Proposals default to .claude/agent-bench/<agent>/proposals.json, a JSON list written by the Sonnet
subagent, one entry per planted case:
  {"id": "<new case id>", "base": "<real case id>", "target": "prompt" | "<path under the base's files/>",
   "find": "<exact text>", "replace": "<text>", "expected": {<expected answer for the edited input>},
   "note": "<why this edit changes the answer>"}
For each proposal the script checks the base is a frozen real case, the target exists, "find" differs
from "replace", and "find" occurs EXACTLY ONCE in the target. Zero matches or two-plus matches reject
the proposal (reason printed, case never written). Accepted proposals copy the base fixture, apply the
edit, hash it into cases.lock.json and append a planted case (base, edit, expected) to cases.jsonl.
Prints a unified diff of each accepted edit against its clean base. Re-planting an id replaces it.
"""
import difflib
import os
import sys

import bench_lib as L
import cases_lib as C


def target_rel(base, target):
    if target == "prompt":
        return "prompt.txt"
    for f in base["input"]["files"]:
        if target in (f["path"], f["fixture"], f["fixture"][len("files/"):]):
            return f["fixture"]
    return None


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    agent = sys.argv[1]
    pp = sys.argv[2] if len(sys.argv) == 3 else os.path.join(L.bench_dir(), agent, "proposals.json")
    props = L.read_json(pp)
    if not isinstance(props, list) or not props:
        sys.exit(f"no proposals at {pp}")
    cases = C.load_cases(agent)
    by_id = {c["id"]: c for c in cases}
    accepted, rejected = 0, []
    for p in props:
        pid = p.get("id", "?")
        why = None
        base = by_id.get(p.get("base"))
        find, repl = p.get("find"), p.get("replace")
        if not base or base["kind"] != "real":
            why = f"base {p.get('base')!r} is not a frozen real case"
        elif not pid or pid in by_id and by_id[pid]["kind"] != "planted":
            why = f"id {pid!r} missing or collides with a non-planted case"
        elif not isinstance(find, str) or not find or not isinstance(repl, str) or find == repl:
            why = "find must be non-empty text and differ from replace"
        elif not isinstance(p.get("expected"), dict) or not p["expected"]:
            why = "no expected answer"
        else:
            rel = target_rel(base, p.get("target", "prompt"))
            if not rel:
                why = f"target {p.get('target')!r} not in the base's frozen inputs"
            else:
                src = os.path.join(C.fixtures_dir(agent), base["id"], rel)
                text = open(src, encoding="utf-8").read()
                n = text.count(find)
                if n != 1:
                    why = f"find matched {n} times in {rel} (must be exactly once)"
        if why:
            rejected.append({"id": pid, "reason": why})
            print(f"REJECTED {pid}: {why}")
            continue
        dst = C.copy_fixture(agent, base["id"], pid)
        path = os.path.join(dst, rel)
        new = text.replace(find, repl, 1)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        C.lock_case(agent, pid)
        case = {"id": pid, "agent": agent, "kind": "planted", "base": base["id"],
                "input": {"prompt_file": f"fixtures/{pid}/prompt.txt",
                          "files": [dict(f, sha256=L.sha256_file(os.path.join(dst, f["fixture"]))) for f in base["input"]["files"]],
                          "omitted": base["input"]["omitted"]},
                "edit": {"target": p.get("target", "prompt"), "file": rel, "find": find, "replace": repl},
                "expected": p["expected"], "expected_source": "planted-edit"}
        if p.get("note"):
            case["note"] = p["note"]
        by_id[pid] = case
        cases = [c for c in cases if c["id"] != pid] + [case]
        accepted += 1
        print(f"accepted {pid} (base {base['id']}, {rel})")
        for ln in difflib.unified_diff(text.splitlines(), new.splitlines(), f"{base['id']}/{rel}", f"{pid}/{rel}", lineterm="", n=0):
            print("   " + ln[:240])
    C.write_cases(agent, cases)
    print(f"{accepted} planted case(s) written, {len(rejected)} rejected")


if __name__ == "__main__":
    main()
