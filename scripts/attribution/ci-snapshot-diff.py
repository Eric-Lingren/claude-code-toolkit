#!/usr/bin/env python3
"""ci-snapshot-diff.py — compare per-test-file results of a PR base against its head.

What: reads two run-all-tests.sh JSON results (see the header of run-all-tests.sh for the
  shape) and sorts every test file into newly failing (pass on base, fail on head), newly
  passing (fail on base, pass on head), unchanged (same status on both), added (only on head)
  and removed (only on base). The set of passing files "changed" when the base and head
  passing sets differ. Prints the comparison as JSON on stdout. With --comment-out it also
  writes a Markdown PR comment to that path, but only when the passing set changed; identical
  results write no file, so there is no comment text.
When: from the informational Test snapshot workflow (.github/workflows/test-snapshot.yml)
  after running the suite on the PR base and head. Passive: reads two files, writes at most
  the comment file. Always exits 0 on a completed comparison (a changed set is not an error);
  exits 2 on bad arguments or an unreadable/malformed input file.

Usage:
  ci-snapshot-diff.py --base BASE.json --head HEAD.json [--comment-out PATH]

Importable: load_results, compare, build_comment, COMMENT_MARKER.
"""
import argparse
import json
import sys
from pathlib import Path

COMMENT_MARKER = "<!-- test-snapshot-diff -->"
STATUSES = ("pass", "fail")


def load_results(path):
    """Return {test file: "pass"|"fail"} from a run-all-tests.sh JSON file."""
    data = json.loads(Path(path).read_text())
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, dict):
        raise ValueError(f"{path}: missing 'results' object")
    for name, status in results.items():
        if status not in STATUSES:
            raise ValueError(f"{path}: {name}: status must be pass or fail, got {status!r}")
    return results


def compare(base, head):
    """Compare two {file: status} maps. All lists are sorted."""
    newly_failing, newly_passing, unchanged = [], [], []
    for name in sorted(base.keys() & head.keys()):
        if base[name] == head[name]:
            unchanged.append(name)
        elif head[name] == "fail":
            newly_failing.append(name)
        else:
            newly_passing.append(name)
    added = [{"file": n, "status": head[n]} for n in sorted(head.keys() - base.keys())]
    removed = [{"file": n, "status": base[n]} for n in sorted(base.keys() - head.keys())]
    base_pass = {n for n, s in base.items() if s == "pass"}
    head_pass = {n for n, s in head.items() if s == "pass"}
    return {
        "newly_failing": newly_failing,
        "newly_passing": newly_passing,
        "unchanged": unchanged,
        "added": added,
        "removed": removed,
        "passing_set_changed": base_pass != head_pass,
        "totals": {
            "base_passing": len(base_pass),
            "head_passing": len(head_pass),
            "base_total": len(base),
            "head_total": len(head),
        },
    }


def _bullets(items):
    return [f"- `{i}`" for i in items]


def build_comment(diff):
    """Markdown PR comment text, or "" when the set of passing files did not change."""
    if not diff["passing_set_changed"]:
        return ""
    t = diff["totals"]
    lines = [
        COMMENT_MARKER,
        "### Test snapshot: passing files changed",
        "",
        f"Passing test files: {t['base_passing']}/{t['base_total']} on base, "
        f"{t['head_passing']}/{t['head_total']} on this PR.",
        "",
    ]
    if diff["newly_failing"]:
        lines += [f"**Newly failing ({len(diff['newly_failing'])})**", *_bullets(diff["newly_failing"]), ""]
    if diff["newly_passing"]:
        lines += [f"**Newly passing ({len(diff['newly_passing'])})**", *_bullets(diff["newly_passing"]), ""]
    added = [a for a in diff["added"] if a["status"] == "pass"]
    if added:
        lines += [f"**New passing files ({len(added)})**", *_bullets(a["file"] for a in added), ""]
    removed = [r for r in diff["removed"] if r["status"] == "pass"]
    if removed:
        lines += [f"**Previously passing files no longer run ({len(removed)})**",
                  *_bullets(r["file"] for r in removed), ""]
    lines.append(f"{len(diff['unchanged'])} files unchanged. Informational only: this does not block the PR.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Compare base vs head per-test-file results.")
    ap.add_argument("--base", required=True, help="run-all-tests.sh JSON for the PR base")
    ap.add_argument("--head", required=True, help="run-all-tests.sh JSON for the PR head")
    ap.add_argument("--comment-out", help="write the PR comment here, only when the passing set changed")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        diff = compare(load_results(args.base), load_results(args.head))
    except (OSError, ValueError) as e:
        print(f"ci-snapshot-diff: {e}", file=sys.stderr)
        return 2
    comment = build_comment(diff)
    if args.comment_out and comment:
        Path(args.comment_out).write_text(comment)
    print(json.dumps(diff, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
