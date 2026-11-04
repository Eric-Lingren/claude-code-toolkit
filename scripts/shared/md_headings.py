"""md_headings.py — the one markdown heading parser shared by the escape-point tooling.

validate-escape-point.py (does a proposed section exist?), write-change-records.py (which
sections did a change edit?) and load-change-diff.py (which hunks fall under a section?) must
agree on what a heading is, or a section the validator accepts never matches a recorded one.

Rules (CommonMark-style ATX headings):
  - 0-3 leading spaces, 1-6 '#' marks, then a space or tab (or end of line).
  - Closing '#' marks ("## Foo ##") are not part of the heading text.
  - Lines inside a fenced code block (``` or ~~~, 0-3 leading spaces) are not headings. A fence
    closes only on the same marker that opened it.

Importable: iter_headings, heading_line, heading_map, normalize_section.
"""
import re

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")
_CLOSING_HASHES_RE = re.compile(r"[ \t]+#+$")


def normalize_section(section):
    """Heading text with leading '#' marks and surrounding whitespace stripped."""
    return (section or "").lstrip("#").strip()


def split_lines(text):
    """Lines split on newline (\\n) only, the way git counts them.

    str.splitlines also splits on U+2028/2029, \\x0b, \\x0c, \\x1c-\\x1e, \\x85 and bare \\r,
    which would shift every later line number away from git's hunk numbers.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def iter_headings(text):
    """Yield (line_number, level, title) for each heading outside fenced code; lines are 1-based."""
    fence = None
    for n, line in enumerate(split_lines(text), start=1):
        fm = FENCE_RE.match(line)
        if fm:
            marker = fm.group(1)
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is not None:
            continue
        hm = HEADING_RE.match(line)
        if hm:
            title = _CLOSING_HASHES_RE.sub("", (hm.group(2) or "").strip()).strip()
            yield n, len(hm.group(1)), title


def heading_line(level, title):
    """Canonical heading line, e.g. '## Instrument'. Used as the recorded section value."""
    return f"{'#' * level} {title}" if title else "#" * level


def heading_map(text):
    """For each 1-based line number, the canonical heading in effect (None before the first)."""
    heads = {n: heading_line(level, title) for n, level, title in iter_headings(text)}
    current = None
    by_line = [None]  # index 0 unused
    for n in range(1, len(split_lines(text)) + 1):
        current = heads.get(n, current)
        by_line.append(current)
    return by_line
