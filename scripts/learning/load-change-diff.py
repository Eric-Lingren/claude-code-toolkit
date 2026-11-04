#!/usr/bin/env python3
"""load-change-diff.py — load the real diff of a change for one escape-point path and section.

What: given a change record, a path and a section heading, prints the git diff of that path
  between the record's base_sha and merged_sha, plus the part of it that falls under the
  section. Used to check a regression_of tag against what the change actually edited
  (see "Regression check" in agents/learning/artifact-grounding-judge.md and log-learning.py).
  Deterministic: no model call, read-only git.

Output (JSON on stdout):
    {"change_id", "path", "section", "base_sha", "merged_sha",
     "section_edited": bool,     # a hunk of the diff falls under the section
     "diff": "<full diff of the path>",
     "section_diff": "<file header + only the hunks under the section>"}

path is the escape-point path, with or without the leading "claude-code-shared/". section is
a heading, with or without leading "#" marks. Both are matched on normalized values, as in
tag-attribution.py. A path that is not in the change's touched_paths is an error.

Usage:
    load-change-diff.py --change-id chg-<sha12> --path P --section S
                        [--repo DIR] [--changes-dir DIR]

Defaults: --repo ~/.dotfiles, --changes-dir <repo>/attribution/change-records.
Exit 0 on success. Exit 1 (message on stderr) when the record, commits or path cannot be
loaded.

Also importable (via importlib): load_change_diff(change, path, section, repo).
"""

import argparse
import importlib.util
import json
import pathlib
import re
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
SHARED = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(SHARED / "scripts" / "shared"))

from change_records_path import change_records_path  # noqa: E402
from md_headings import normalize_section  # noqa: E402
PATH_PREFIX = "claude-code-shared/"
SHA_RE = re.compile(r"[0-9a-f]{40}")
# Per-call limit on the git calls in load_change_diff, so a stuck git cannot hold up a learning.
GIT_TIMEOUT_SECONDS = 10
HUNK_START_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _records():
    spec = importlib.util.spec_from_file_location(
        "write_change_records", SHARED / "scripts" / "attribution" / "write-change-records.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm_path(path):
    path = (path or "").strip()
    return path[len(PATH_PREFIX):] if path.startswith(PATH_PREFIX) else path


def _norm_section(section):
    return normalize_section(section)


def _split_hunks(diff_text):
    """(file header text, [hunk text, ...]) from a unified diff of one file."""
    header, hunks, current = [], [], None
    for line in re.findall(r"[^\n]*\n|[^\n]+", diff_text):
        if HUNK_START_RE.match(line):
            current = [line]
            hunks.append(current)
        elif current is None:
            header.append(line)
        else:
            current.append(line)
    return "".join(header), ["".join(h) for h in hunks]


def _hunk_in_section(hunk, by_line, section):
    m = HUNK_START_RE.match(hunk)
    start = int(m.group(1))
    count = int(m.group(2)) if m.group(2) is not None else 1
    last = len(by_line) - 1
    lines = range(start, start + count) if count > 0 else [start]
    for n in lines:
        n = min(max(n, 0), last)
        heading = by_line[n] if n >= 1 else None
        if _norm_section(heading) == section:
            return True
    return False


def load_change_diff(change, path, section, repo):
    """Diff of `path` in `change`, and the portion under `section`. Raises ValueError."""
    rec = _records()
    rel = _norm_path(path)
    section = _norm_section(section)
    if not rel or not section:
        raise ValueError("path and section are required")
    touched = {_norm_path(p) for p in change.get("touched_paths") or []}
    if rel not in touched:
        raise ValueError(f"{rel} is not in the touched_paths of {change.get('change_id')}")
    base, merged = change.get("base_sha"), change.get("merged_sha")
    if not base or not merged:
        raise ValueError("change record has no base_sha/merged_sha")
    # Both go to git before `--`, so a value like "--output=<path>" would be read as an option.
    for name, sha in (("base_sha", base), ("merged_sha", merged)):
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise ValueError(f"{name} is not a 40-hex sha")
    full = PATH_PREFIX + rel

    try:
        t = GIT_TIMEOUT_SECONDS
        diff = rec.git(repo, "diff", "--no-renames", base, merged, "--", full, timeout=t).stdout
        zero = rec.git(repo, "diff", "-U0", "--no-renames", base, merged, "--", full, timeout=t).stdout
        shown = rec.git(repo, "show", f"{merged}:{full}", check=False, timeout=t)
    except Exception as e:  # CalledProcessError, TimeoutExpired, OSError
        raise ValueError(f"could not load diff {base[:12]}..{merged[:12]} for {full}: {e}") from e
    if not diff.strip():
        raise ValueError(f"no diff for {full} between {base[:12]} and {merged[:12]}")

    section_diff = ""
    if shown.returncode == 0:
        by_line = rec.heading_map(shown.stdout)
        header, hunks = _split_hunks(zero)
        picked = [h for h in hunks if _hunk_in_section(h, by_line, section)]
        if picked:
            section_diff = header + "".join(picked)

    return {
        "change_id": change.get("change_id"),
        "path": rel,
        "section": section,
        "base_sha": base,
        "merged_sha": merged,
        "section_edited": bool(section_diff),
        "diff": diff,
        "section_diff": section_diff,
    }


def _find_change(changes_dir, change_id):
    p = pathlib.Path(changes_dir) / f"{change_id}.json"
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"cannot read change record {p}: {e}") from e
    if not isinstance(rec, dict):
        raise ValueError(f"change record {p} is not a JSON object")
    return rec


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--section", required=True)
    parser.add_argument("--repo", default=str(pathlib.Path.home() / ".dotfiles"))
    parser.add_argument("--changes-dir", default=None)
    args = parser.parse_args()

    changes_dir = args.changes_dir or change_records_path()
    try:
        change = _find_change(changes_dir, args.change_id)
        result = load_change_diff(change, args.path, args.section, args.repo)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
