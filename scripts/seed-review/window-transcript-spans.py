#!/usr/bin/env python3
"""window-transcript-spans.py — build a judge evidence pack from a cleaned transcript.

Given a cleaned transcript and the merged refutation list, emit a small "evidence
pack" file containing only a windowed slice of the transcript around each
refutation's cited span. Judges read this pack instead of re-reading the full
transcript, which is the dominant token cost in the to-seed verification stage.

Each refutation must carry a stable `ref_id` (minted by the orchestrator) and a
`transcript_span` (verbatim quote, or null for absence-type claims).

Output sections, one per ref_id:
  ## <ref_id>
  <windowed context, or a marker if the span is absent / not found>

Markers:
  ABSENCE CLAIM — no transcript span; adjudicate via seed.
  SPAN NOT FOUND IN TRANSCRIPT — span text does not appear; treat as unsupported.

Usage:
  python3 window-transcript-spans.py <cleaned-transcript> <refutations-json> <output-pack>

Exit codes:
  0  Success — pack written
  1  Error — missing args or unreadable input
"""

import sys
import json
import re
import pathlib

CHARS_BEFORE = 600      # chars before the match on the hit line
CHARS_AFTER = 1000      # chars after the match on the hit line
NEIGHBOR_CAP = 500      # chars kept from each adjacent line
WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace for fuzzy substring matching."""
    return WS.sub(" ", text).strip().lower()


def cap_neighbor(line: str) -> str:
    line = line.rstrip("\n")
    if len(line) <= NEIGHBOR_CAP:
        return line
    return line[:NEIGHBOR_CAP] + " […]"


def window_for_span(lines, norm_lines, span: str) -> str:
    """Return a windowed context string for span, or None if not found."""
    span_norm = normalize(span)
    if not span_norm:
        return None
    for i, ln in enumerate(norm_lines):
        pos = ln.find(span_norm)
        if pos == -1:
            continue
        # Found on line i. Slice the raw line around an approximate match offset.
        raw = lines[i].rstrip("\n")
        # Map normalized offset back to a rough raw offset (best-effort).
        approx = min(pos, max(0, len(raw) - 1))
        start = max(0, approx - CHARS_BEFORE)
        end = min(len(raw), approx + len(span) + CHARS_AFTER)
        hit = raw[start:end]
        if start > 0:
            hit = "[…] " + hit
        if end < len(raw):
            hit = hit + " […]"
        parts = []
        if i > 0:
            parts.append(cap_neighbor(lines[i - 1]))
        parts.append(hit)
        if i + 1 < len(lines):
            parts.append(cap_neighbor(lines[i + 1]))
        return "\n".join(parts)
    return None


def main():
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)
    if len(args) != 3:
        print(
            "ERROR: expected 3 args.\n"
            "Usage: window-transcript-spans.py <cleaned-transcript> <refutations-json> <output-pack>",
            file=sys.stderr,
        )
        sys.exit(1)

    transcript_path, refutations_path, output_path = (pathlib.Path(a) for a in args)

    if not transcript_path.exists():
        print(f"ERROR: cleaned transcript not found: {transcript_path}", file=sys.stderr)
        sys.exit(1)
    if not refutations_path.exists():
        print(f"ERROR: refutations file not found: {refutations_path}", file=sys.stderr)
        sys.exit(1)

    try:
        refutations = json.loads(refutations_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: refutations file is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(refutations, list):
        print("ERROR: refutations file must contain a JSON array", file=sys.stderr)
        sys.exit(1)

    lines = transcript_path.read_text(encoding="utf-8", errors="replace").splitlines()
    norm_lines = [normalize(ln) for ln in lines]

    sections = []
    found = 0
    for ref in refutations:
        ref_id = ref.get("ref_id", "?") if isinstance(ref, dict) else "?"
        span = ref.get("transcript_span") if isinstance(ref, dict) else None
        if span is None:
            body = "ABSENCE CLAIM — no transcript span; adjudicate via seed."
        else:
            ctx = window_for_span(lines, norm_lines, str(span))
            if ctx is None:
                body = "SPAN NOT FOUND IN TRANSCRIPT — span text does not appear; treat as unsupported."
            else:
                body = ctx
                found += 1
        sections.append(f"## {ref_id}\n{body}\n")

    output_path.write_text("\n".join(sections), encoding="utf-8")
    print(
        f"OK: {len(refutations)} refutations, {found} spans located, "
        f"pack written to {output_path} ({output_path.stat().st_size:,}B)"
    )


if __name__ == "__main__":
    main()
