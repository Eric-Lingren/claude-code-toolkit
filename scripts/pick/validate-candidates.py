#!/usr/bin/env python3
"""Validate normalized /pick candidate JSON (stdin or file arg). Exit 0 if valid."""
import json, sys

REQ = {"id": str, "source": str, "repo": str, "title": str, "url": str,
       "labels": list, "assignees": list, "state": str, "blockers": list, "branch": str}
OPT = {"points": (int, float, type(None)), "parent": (str, type(None)),
       "project": (str, type(None)), "created_at": (str, type(None))}


def errors(doc):
    errs = []
    for k in ("repo", "bucket", "sort", "candidates"):
        if k not in doc:
            errs.append(f"missing top-level key {k}")
    for n, c in enumerate(doc.get("candidates", [])):
        for k, t in REQ.items():
            if not isinstance(c.get(k), t):
                errs.append(f"candidates[{n}].{k} missing or wrong type")
        for k, t in OPT.items():
            if k not in c or not isinstance(c[k], t):
                errs.append(f"candidates[{n}].{k} missing or wrong type")
        if c.get("source") not in ("github", "linear"):
            errs.append(f"candidates[{n}].source invalid")
    return errs


if __name__ == "__main__":
    data = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    errs = errors(json.loads(data))
    for e in errs:
        print(e, file=sys.stderr)
    sys.exit(1 if errs else 0)
