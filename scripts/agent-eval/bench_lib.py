"""Shared helpers for the improve-agent-benchmarks scripts (paths, queue, grading, citations).

Paths (override with env for scratch runs, no flags needed):
  AGENT_BENCH_DIR  default <repo>/.claude/agent-bench   (queue.json, <agent>/history, <agent>/draft.contract.json)
  AGENT_EVALS_DIR  default <repo>/claude-code-shared/evals   (<agent>/contract.json, <agent>/baseline.json)
Cited files in contracts are relative to <repo>/claude-code-shared/ (SHARED_DIR).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import grade  # noqa: E402  (shared deterministic grader)

BUILTIN_TYPES = ["json_parse", "schema", "quote_in_input", "tool_called", "tool_not_called", "file_written", "verdict_equals", "no_prose"]
SOURCE_TAGS = ["role", "caller", "side_effect"]
STAGES = ["contract", "cases", "iterate"]


def repo_root():
    return subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True, cwd=HERE).strip()


def bench_dir():
    return os.environ.get("AGENT_BENCH_DIR") or os.path.join(repo_root(), ".claude", "agent-bench")


def evals_dir():
    return os.environ.get("AGENT_EVALS_DIR") or os.path.join(SHARED_DIR, "evals")


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p) as fh:
        return json.load(fh)


def write_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, p)


def sha256_file(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def parse_lines(spec):
    """'12' or '12-18' -> (start, end) 1-based inclusive."""
    m = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", str(spec))
    if not m:
        return None
    a = int(m.group(1))
    return a, int(m.group(2) or a)


def cited_path(f):
    p = os.path.normpath(os.path.join(SHARED_DIR, f))
    return p if p.startswith(SHARED_DIR + os.sep) else None


def validate_source(src):
    """Return None when the citation is real, else a reason string."""
    if not isinstance(src, dict):
        return "no source"
    if src.get("tag") not in SOURCE_TAGS:
        return f"source tag must be one of {SOURCE_TAGS}"
    f, line, quote = src.get("file"), src.get("line"), src.get("quote")
    if not f or line is None or not quote:
        return "source needs file, line and quote"
    p = cited_path(f)
    if not p or not os.path.isfile(p):
        return f"cited file not found: {f}"
    span = parse_lines(line)
    if not span:
        return f"bad line spec: {line!r}"
    with open(p, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    a, b = span
    if a < 1 or b > len(lines) or a > b:
        return f"line {line} outside {f} ({len(lines)} lines)"
    window = norm(" ".join(lines[max(0, a - 2): b + 1]))
    if norm(quote) not in window:
        return f"quote not found at {f}:{line}"
    return None


def load_history(agent):
    d = os.path.join(bench_dir(), agent, "history")
    if not os.path.isdir(d):
        return []
    return grade.load_records([d])


def agent_file(agent):
    for root, _, files in os.walk(os.path.join(SHARED_DIR, "agents")):
        if agent + ".md" in files:
            return os.path.join(root, agent + ".md")
    return None


def agent_changed_at(agent):
    """(datetime, short sha) of the last commit touching the agent's .md, or None (no file / never committed)."""
    p = agent_file(agent)
    if not p:
        return None
    try:
        out = subprocess.check_output(["git", "log", "-1", "--format=%cI %h", "--", p], text=True,
                                      cwd=os.path.dirname(p), stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    if not out:
        return None
    ts, sha = out.split()
    return datetime.fromisoformat(ts), sha


def rec_time(rec):
    try:
        return datetime.fromisoformat((rec.get("timestamp") or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def current_history(agent, recs):
    """Spawns made by the agent's current version: timestamp at or after the last commit to its .md.
    Older spawns graded a different prompt, so they say nothing about the agent as it is now. Undated
    spawns are dropped. Returns (kept records, note for the caller to print)."""
    ch = agent_changed_at(agent)
    if not ch:
        return recs, "history window: all spawns (agent file has no commit)"
    since, sha = ch
    kept = [r for r in recs if (rec_time(r) or since.min.replace(tzinfo=timezone.utc)) >= since]
    return kept, (f"history window: {len(kept)} of {len(recs)} spawns since the agent file last changed "
                  f"({since.astimezone(timezone.utc):%Y-%m-%d %H:%M}Z, {sha})")


def grade_contract(contract, recs):
    """Per-check stats using grade.check (same engine as grade.py). Returns list of dicts."""
    out = []
    for c in contract["checks"]:
        p = f = n = 0
        fails = []
        for r in recs:
            res, detail = grade.check(c, r)
            if res is None:
                n += 1
            elif res:
                p += 1
            else:
                f += 1
                fails.append({"agent_id": r.get("agent_id", "?"), "timestamp": r.get("timestamp"),
                              "detail": detail, "output_head": norm(r.get("final_output"))[:140]})
        tot = p + f
        out.append({"id": c["id"], "type": c["type"], "pass": p, "fail": f, "na": n,
                    "rate": round(p / tot, 4) if tot else None, "fails": fails})
    return out


def queue_path():
    return os.path.join(bench_dir(), "queue.json")


def load_queue():
    return read_json(queue_path(), {"version": 1, "agents": {}})


def agent_names():
    names = set()
    for root, _, files in os.walk(os.path.join(SHARED_DIR, "agents")):
        for fn in files:
            if fn.endswith(".md"):
                names.add(fn[:-3])
    return sorted(names)


def draft_path(agent):
    return os.path.join(bench_dir(), agent, "draft.contract.json")
