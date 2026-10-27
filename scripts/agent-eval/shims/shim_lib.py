"""Shared helper for the live-runner shims: append one JSON line to the shim log ($SHIM_LOG)."""
import fcntl
import json
import os
import sys
from datetime import datetime, timezone


def record(entry):
    """Append entry (+ts) to $SHIM_LOG. Refuses to run outside a runner sandbox."""
    log = os.environ.get("SHIM_LOG")
    if not log:
        print("shim: SHIM_LOG not set - refusing to run outside the agent-bench sandbox", file=sys.stderr)
        sys.exit(97)
    entry = dict(entry, ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    with open(log, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
