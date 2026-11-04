#!/usr/bin/env python3
"""validate-escape-point.py — deterministically validate an escape_point proposal.

Reads a JSON escape_point proposal {path, section} on stdin and prints the
validated form as JSON on stdout:

    - path missing / not a file in the repo  -> prints `null` (drop the escape_point)
    - path exists, section heading not found -> {"path": ..., "section": null}
    - path exists, heading found (or section null) -> proposal unchanged

Heading match is exact on the heading text, ignoring the leading '#' marks
(and surrounding whitespace). Any heading level matches, so nested headings
work. Headings inside fenced code blocks are ignored. Matching is against the
file as it is in the working tree (the current sha).

The path is resolved against the repo root, then against <root>/claude-code-shared
(so `skills/debug/SKILL.md` resolves). Absolute paths and paths that escape the
root are treated as nonexistent.

Usage:
    echo '{"path":"skills/debug/SKILL.md","section":"## Instrument"}' \\
        | python validate-escape-point.py [--root DIR]

Root: --root, else $VALIDATE_ESCAPE_POINT_ROOT, else ~/.dotfiles.

Exit 0 always for a well-formed run (stdout carries the verdict). Exit 1 only
when stdin is not valid JSON.

Also importable (via importlib) for `validate_escape_point(proposal, root)`.
"""

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "shared"))

from md_headings import iter_headings, normalize_section  # noqa: E402


def _resolve(root, rel):
    """Return the existing file path for rel under root, or None."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        return None
    root = pathlib.Path(root).resolve()
    for base in (root, root / "claude-code-shared"):
        candidate = (base / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _headings(text):
    """Heading texts in the file, skipping fenced code blocks."""
    return [title for _, _, title in iter_headings(text)]


def validate_escape_point(proposal, root):
    """Return the validated escape_point dict, or None when it must be dropped."""
    if not isinstance(proposal, dict):
        return None
    path = proposal.get("path")
    file_path = _resolve(root, path)
    if file_path is None:
        return None

    section = proposal.get("section")
    if not isinstance(section, str):
        return {"path": path, "section": None}
    wanted = normalize_section(section)
    if not wanted:
        return {"path": path, "section": None}
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"path": path, "section": None}
    if wanted in _headings(text):
        return {"path": path, "section": section}
    return {"path": path, "section": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None)
    args = parser.parse_args()
    root = (
        args.root
        or os.environ.get("VALIDATE_ESCAPE_POINT_ROOT")
        or str(pathlib.Path.home() / ".dotfiles")
    )
    try:
        proposal = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(f"ERROR: stdin is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(validate_escape_point(proposal, root)))


if __name__ == "__main__":
    main()
