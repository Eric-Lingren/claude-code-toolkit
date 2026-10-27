#!/usr/bin/env python3
"""Shim for scripts/log-learning.py. Records the call (args + stdin) in the shim log, appends the
entry to the per-run temp learnings file ($SHIM_LEARNINGS), prints canned success, exits 0.
Never touches the real unified-learnings.jsonl."""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shim_lib  # noqa: E402

stdin = sys.stdin.read()
dest = os.environ.get("SHIM_LEARNINGS", "")
try:
    entry = json.loads(stdin)
except ValueError:
    entry = None
etype = entry.get("type", "?") if isinstance(entry, dict) else "?"
lid = str(uuid.uuid4())
if dest and isinstance(entry, dict):
    with open(dest, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(entry, id=lid), ensure_ascii=False, separators=(",", ":")) + "\n")
shim_lib.record({"shim": "log-learning.py", "tool": "Bash", "args": sys.argv[1:], "stdin": stdin,
                 "input": {"command": "log-learning.py " + " ".join(sys.argv[1:]), "stdin": stdin},
                 "writes": [dest] if dest else [], "returned": f"OK: appended learning entry (type={etype}, id={lid})"})
lesson = " ".join(str(entry.get("lesson", "")).split()) if isinstance(entry, dict) else ""
print(f"OK: appended learning entry (type={etype}, id={lid}) to {dest or 'unified-learnings.jsonl'} | lesson: {lesson}")
