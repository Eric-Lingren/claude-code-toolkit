#!/usr/bin/env python3
"""PreToolUse hook injected via --settings for every live run (the only hook that runs).

Bash: puts shims/ first on PATH, rewrites any path to log-learning.py (agents call it by absolute
      ~/.dotfiles path, which PATH alone cannot intercept) to the shim, and denies commands that would
      write the real unified-learnings.jsonl.
Agent/Task: never spawns the child. Looks up the child's real recorded output ($SHIM_CHILDREN, built by
      child_lookup.py from the same session log), records the call in the shim log and denies the spawn
      with that output as the tool result.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import shim_lib  # noqa: E402

LOG_LEARNING = re.compile(r"[^\s'\"|;&]*log-learning\.py")
SHARED_SCRIPTS = re.compile(r"(?:~|\$HOME|\$\{HOME\}|/Users/[\w.-]+)/\.(?:dotfiles/)?claude-code-shared/scripts/")
GH_ABS = re.compile(r"(?<![\w./-])/[^\s'\"|;&]*/gh(?=[\s|;&]|$)")
WRITE_OP = re.compile(r"(>|\btee\b|\bsed\s+-i|\bmv\b|\bcp\b|\brm\b|\btruncate\b|\bdd\b)")


def out(decision, reason=None, updated=None):
    o = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason:
        o["permissionDecisionReason"] = reason
    if updated is not None:
        o["updatedInput"] = updated
    print(json.dumps({"hookSpecificOutput": o}))
    sys.exit(0)


def bash(ti):
    cmd = ti.get("command", "")
    if "unified-learnings.jsonl" in cmd and WRITE_OP.search(cmd):
        shim_lib.record({"shim": "blocked-learnings-write", "tool": "Bash", "input": {"command": cmd}})
        out("deny", "Blocked: writing unified-learnings.jsonl directly is not allowed in this sandbox.")
    new = LOG_LEARNING.sub(os.path.join(HERE, "log-learning.py"), cmd)
    if os.environ.get("SHIM_SCRIPTS_DIR"):
        new = SHARED_SCRIPTS.sub(os.environ["SHIM_SCRIPTS_DIR"].rstrip("/") + "/", new)
    new = GH_ABS.sub(os.path.join(HERE, "gh"), new)
    new = f'export PATH="{HERE}:$PATH"\n{new}'
    out("allow", updated=dict(ti, command=new))


def child(ti):
    stype = ti.get("subagent_type") or "general-purpose"
    path = os.environ.get("SHIM_CHILDREN")
    kids = json.load(open(path)) if path and os.path.exists(path) else []
    used = (path or "") + ".used"
    taken = set(open(used).read().split()) if path and os.path.exists(used) else set()
    pick = next((k for k in kids if k["agent_type"] == stype and k["agent_id"] not in taken), None)
    if not pick:
        shim_lib.record({"shim": "child-agent", "tool": "Agent", "input": ti, "returned": None, "note": "no recorded child"})
        out("deny", f"SHIM: no recorded output for child agent '{stype}' in this session. Report that the child agent was unavailable.")
    open(used, "a").write(pick["agent_id"] + "\n")
    shim_lib.record({"shim": "child-agent", "tool": "Agent", "input": ti, "child_agent_id": pick["agent_id"],
                     "returned": pick["output"]})
    out("deny", "The child agent already ran. Its final result is below; treat it exactly as the Agent tool result "
                "(do not retry or re-spawn it).\n\n" + pick["output"])


def main():
    ev = json.load(sys.stdin)
    ti = ev.get("tool_input") or {}
    tool = ev.get("tool_name")
    if tool == "Bash":
        bash(ti)
    elif tool in ("Agent", "Task"):
        child(ti)
    out("allow")


if __name__ == "__main__":
    main()
