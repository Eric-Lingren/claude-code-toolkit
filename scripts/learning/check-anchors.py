#!/usr/bin/env python3
"""check-anchors.py — mechanical quote check for artifact-grounding-judge.

Reads a draft v2 attribution record (JSON) on stdin, or from --record-file,
and checks every evidence entry's quote against the file at its ref. The quote
is read straight from the record, so the caller never re-types it.

Matching: whitespace runs collapsed on both sides, then exact substring.

Usage:
  check-anchors.py [--record-file PATH] <<'JSON'
  <draft record JSON>
  JSON

Output: one JSON line per evidence entry:
  {"index": i, "source": ..., "ref": ..., "status": "MATCH"|"MISSING"|"NO_MATCH",
   "words_not_in_file": [...], "closest": "..."}   (last two only on NO_MATCH)
Absence claims ("X does not appear ...") normally report NO_MATCH; the caller
checks them separately.

Exit codes: 0 checked, 1 bad input.
"""
import argparse
import json
import os
import re
import sys

norm = lambda s: re.sub(r"\s+", " ", s).strip()
low = lambda ws: {w.lower() for w in ws}


def check(entry):
    ref = os.path.expanduser(str(entry.get("ref", "")))
    out = {"source": entry.get("source"), "ref": entry.get("ref")}
    if not os.path.isfile(ref):
        return {**out, "status": "MISSING"}
    q = norm(str(entry.get("quote", "")))
    with open(ref, errors="replace") as fh:
        t = norm(fh.read())
    if q and q in t:
        return {**out, "status": "MATCH"}
    words = low(re.findall(r"\w+", t))
    missing = [w for w in re.findall(r"\w+", q) if w.lower() not in words]
    tw, qw = t.split(" "), q.split(" ")
    qs, n = low(qw), len(qw)
    i = max(range(max(1, len(tw) - n + 1)), key=lambda k: len(qs & low(tw[k:k + n])))
    closest = " ".join(tw[max(0, i - 3):i + n + 3])
    return {**out, "status": "NO_MATCH", "words_not_in_file": missing, "closest": closest}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--record-file", help="read the record from PATH instead of stdin")
    a = ap.parse_args()
    try:
        raw = open(a.record_file).read() if a.record_file else sys.stdin.read()
        evidence = json.loads(raw).get("evidence")
        if not isinstance(evidence, list):
            raise ValueError("record has no evidence array")
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for i, entry in enumerate(evidence):
        print(json.dumps({"index": i, **check(entry)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
