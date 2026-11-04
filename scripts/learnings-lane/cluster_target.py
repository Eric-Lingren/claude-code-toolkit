#!/usr/bin/env python3
# cluster_target.py: model half of learnings-lane clustering for one target
# What: cluster_target(target, rows, agent, events_path) calls the learnings-clusterer agent with the rows and
#   their pregroups, runs validate_clusters.errors on the output, and rejects any output that carries an
#   escape_point (the model never writes it). Invalid output is retried once with the errors as feedback; a
#   second failure skips the target and appends a JSON line to the events log. Returns clusters or None.
# CLI: cluster_target.py --target T --input ROWS.json --agent-cmd CMD [--events PATH]
#   CMD gets the request JSON on stdin and prints the agent's JSON on stdout.
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_d = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _d / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


procgroup = _load("procgroup")
pregroup = _load("pregroup").pregroup
validate_clusters = _load("validate_clusters")

DEFAULT_EVENTS = _d.parent.parent / "learnings" / "lane-events.jsonl"
CALL_TIMEOUT_S = 600
CLUSTER_ATTEMPTS = 2
STDERR_TAIL_CHARS = 300


class ClaudeCallError(RuntimeError):
    """A model subprocess exited nonzero or timed out; its output is not a usable answer."""


def call_timeout():
    return float(os.environ.get("CLAUDE_CALL_TIMEOUT_S", CALL_TIMEOUT_S))


def check(rows, out, target=None):
    return validate_clusters.errors(rows, out, target)


def log_event(path, event):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as events_file:
        events_file.write(json.dumps(event) + "\n")


def cluster_target(target, rows, agent, events_path=DEFAULT_EVENTS):
    req = {"target": target, "learnings": rows,
           "pregroups": [[row["id"] for row in group] for group in pregroup(rows)]}
    bad_req = validate_clusters.request_errors(req)
    if bad_req:
        raise ValueError("cluster request violates cluster-contract.json: " + "; ".join(bad_req))
    errs = []
    for _attempt in range(CLUSTER_ATTEMPTS):
        out = agent(dict(req, errors=errs) if errs else req)
        errs = check(rows, out, target)
        if not errs:
            return out
    log_event(events_path, {"event": "cluster_skipped", "target": target, "attempts": CLUSTER_ATTEMPTS,
                            "errors": errs, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return None


def shell_agent(cmd):
    def run(request):
        try:
            proc = procgroup.run_killpg(cmd, shell=True, input=json.dumps(request), text=True,
                                     timeout=call_timeout())
        except subprocess.TimeoutExpired:
            raise ClaudeCallError(f"agent command timed out after {call_timeout()}s")
        if proc.returncode:
            raise ClaudeCallError(
                f"agent command exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-STDERR_TAIL_CHARS:]}")
        try:
            return json.loads(proc.stdout)
        except ValueError:
            return {}
    return run


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--agent-cmd", required=True)
    ap.add_argument("--events", default=str(DEFAULT_EVENTS))
    args = ap.parse_args(argv)
    rows = json.loads(Path(args.input).read_text())
    out = cluster_target(args.target, rows, shell_agent(args.agent_cmd), args.events)
    if out is None:
        print(f"skipped {args.target}: see {args.events}", file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
