#!/usr/bin/env python3
# pregroup.py: deterministic first pass of learnings-lane clustering
# What: pregroup(rows) -> [[row, ...], ...]. Rows join a group when they share improves AND cause AND at
#   least one evidence file (artifact evidence refs plus escape_point.path; transcript refs are per-session
#   and ignored). Joins are transitive. Rows with no file overlap stay singletons, so a cause shared by many
#   rows (general_best_practice covers 206 of 589) never merges them on its own.
# When: before the LLM clustering step; its groups are hints, validate_clusters.py checks the final result.
# CLI: pregroup.py [--file PATH] [--target T] prints JSON groups of learning ids.
import argparse
import json
import sys
from pathlib import Path


def _norm(ref):
    return str(ref).split(":", 1)[0].strip().lstrip("./") if ref else ""


def files_of(row):
    fs = set()
    for e in row.get("evidence") or []:
        if isinstance(e, dict) and e.get("source") == "artifact" and e.get("ref"):
            fs.add(_norm(e["ref"]))
    ep = row.get("escape_point")
    if isinstance(ep, dict) and ep.get("path"):
        fs.add(_norm(ep["path"]))
    fs.discard("")
    return fs


def pregroup(rows):
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    files = [files_of(r) for r in rows]
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if (a.get("improves") == b.get("improves") and a.get("cause") == b.get("cause")
                    and files[i] & files[j]):
                parent[find(j)] = find(i)
    groups = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(i), []).append(r)
    return list(groups.values())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pre-group a target's learnings by improves, cause and evidence files")
    ap.add_argument("--file", default=str(Path(__file__).resolve().parent.parent.parent
                                          / "learnings" / "unified-learnings.jsonl"))
    ap.add_argument("--target")
    args = ap.parse_args(argv)
    rows = []
    for line in Path(args.file).read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    if args.target:
        rows = [r for r in rows if r.get("improves") == args.target]
    print(json.dumps([[r.get("id") for r in g] for g in pregroup(rows)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
