#!/usr/bin/env python3
"""Validate pick-scorer JSON against its contract.

Usage: validate-scorer.py input  FILE   # scorer input (<=25 candidates)
       validate-scorer.py output FILE [--input FILE]  # scorer output; with
                                                      # --input, ids must match 1:1
Reads stdin when FILE is '-'. Exit 0 if valid.
"""
import json, sys

MAX = 25
STACKS = {"fe", "be", "full", "infra"}


def input_errors(doc):
    errs = []
    for k in ("repo", "bucket", "candidates"):
        if k not in doc:
            errs.append(f"missing {k}")
    cs = doc.get("candidates", [])
    if len(cs) > MAX:
        errs.append(f"{len(cs)} candidates exceeds cap {MAX}")
    for n, c in enumerate(cs):
        for k in ("id", "title", "state"):
            if not isinstance(c.get(k), str):
                errs.append(f"candidates[{n}].{k} missing or not a string")
        if not isinstance(c.get("labels"), list):
            errs.append(f"candidates[{n}].labels missing or not a list")
        if "points" not in c or not isinstance(c["points"], (int, float, type(None))):
            errs.append(f"candidates[{n}].points missing or wrong type")
    return errs


def output_errors(doc, inp=None):
    errs = []
    ss = doc.get("scores")
    if not isinstance(ss, list):
        return ["scores missing or not a list"]
    if len(ss) > MAX:
        errs.append(f"{len(ss)} scores exceeds cap {MAX}")
    for n, s in enumerate(ss):
        if not isinstance(s.get("id"), str):
            errs.append(f"scores[{n}].id missing or not a string")
        sc = s.get("score")
        if isinstance(sc, bool) or not isinstance(sc, int) or not 1 <= sc <= 10:
            errs.append(f"scores[{n}].score must be an integer 1-10")
        r = s.get("reason")
        if not isinstance(r, str) or not r.strip():
            errs.append(f"scores[{n}].reason missing or empty")
        elif "\n" in r or len(r) > 120:
            errs.append(f"scores[{n}].reason must be one line, <=120 chars")
        if "stack" in s and s["stack"] not in STACKS:
            errs.append(f"scores[{n}].stack must be one of {sorted(STACKS)}")
    if inp is not None:
        want = [c["id"] for c in inp.get("candidates", [])]
        got = [s.get("id") for s in ss]
        if sorted(want) != sorted(got):
            errs.append("score ids do not match input candidate ids 1:1")
    return errs


def load(p):
    return json.loads(sys.stdin.read() if p == "-" else open(p).read())


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2 or a[0] not in ("input", "output"):
        sys.exit(__doc__)
    inp = load(a[a.index("--input") + 1]) if "--input" in a else None
    doc = load(a[1])
    errs = input_errors(doc) if a[0] == "input" else output_errors(doc, inp)
    for e in errs:
        print(e, file=sys.stderr)
    sys.exit(1 if errs else 0)
