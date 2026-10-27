#!/usr/bin/env python3
"""verify-anchors.py — deterministic grounding check for a v2 self-learning entry.

Replaces the learning-grounding-judge LLM agent. Reads a candidate entry (JSON)
on stdin and confirms every transcript evidence quote appears in the session
transcript, verbatim or near-verbatim.

Haystack: the main session JSONL (tool inputs/results uncapped) plus, when
present, the session's sibling dir: subagents/*.jsonl and tool-results/* (large
tool outputs Claude Code persists outside the main log).

Matching. Both sides normalized: JSON escapes undone, lowercased, every run of
non-alphanumeric chars collapsed to one space. So punctuation, Markdown, JSON
spacing and smart quotes never block a match, but word order must hold.
  1. Quote split on ellipses ("..." / "…"); every fragment must match.
  2. A fragment matches if it is a substring of the haystack, OR (fragments
     of 6+ words) at least 85% of its 6-word shingles are substrings.

Usage:
  echo '<entry json>' | verify-anchors.py --transcript PATH [--write]

  --transcript  session JSONL or a prep-transcript.py .txt render
  --write       on grounded=true, strip server-injected fields and append via
                log-learning.py (its output is printed after the verdict)

Output: one JSON line {"grounded": bool, "reason": str, "anchors": [...]}

Exit codes:
  0  grounded (and written, if --write)
  1  error (bad input, unreadable transcript, log-learning.py failure)
  2  not grounded
"""

import argparse
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
SERVER_FIELDS = ("schema_version", "id", "timestamp")
SHINGLE = 6
SHINGLE_THRESHOLD = 0.85
ELLIPSIS = re.compile(r"\.\.\.+|…")
NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text):
    text = text.replace("\\n", " ").replace("\\t", " ").replace('\\"', '"')
    return " " + NON_ALNUM.sub(" ", text.lower()).strip() + " "


def load_prep():
    spec = importlib.util.spec_from_file_location("prep_transcript", SCRIPT_DIR / "prep-transcript.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_transcript(path):
    p = pathlib.Path(path)
    if p.suffix != ".jsonl":
        return normalize(p.read_text(encoding="utf-8", errors="replace"))
    prep = load_prep()
    parts = prep.render(p, capped=False)
    session_dir = p.with_suffix("")
    if session_dir.is_dir():
        for sub in sorted(session_dir.glob("subagents/*.jsonl")):
            parts.extend(prep.render(sub, capped=False))
        for res in sorted(session_dir.glob("tool-results/*")):
            if res.is_file():
                parts.append(res.read_text(encoding="utf-8", errors="replace"))
    # Drop the "[L123] role:" prefixes so quotes spanning blocks still match.
    text = re.sub(r"\[L\d+\] [\w/]+(?: [\w.:-]+)?: ", " ", "\n".join(parts))
    return normalize(text)


def fragment_matches(frag, hay):
    if f" {frag} " in hay:
        return True
    words = frag.split()
    if len(words) < SHINGLE:
        return False
    shingles = [" ".join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)]
    hits = sum(1 for s in shingles if f" {s} " in hay)
    return hits / len(shingles) >= SHINGLE_THRESHOLD


def check_quote(quote, hay):
    frags = [f.strip() for f in (normalize(x) for x in ELLIPSIS.split(quote)) if f.strip()]
    if len(frags) > 1:
        # Drop 1-word stubs around ellipses; they match anything.
        frags = [f for f in frags if len(f.split()) >= 2]
    if not frags:
        return False
    return all(fragment_matches(f, hay) for f in frags)


def extract_quotes(evidence):
    if isinstance(evidence, list):
        return [e.get("quote", "") for e in evidence
                if isinstance(e, dict) and e.get("source", "transcript") == "transcript"]
    if isinstance(evidence, str):  # v1 string evidence: pull quoted spans
        return [a or b for a, b in re.findall(r"'([^']{8,})'|\"([^\"]{8,})\"", evidence)]
    return []


def verdict(grounded, reason, anchors=()):
    print(json.dumps({"grounded": grounded, "reason": reason, "anchors": list(anchors)}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    try:
        entry = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(f"ERROR: entry is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        hay = load_transcript(args.transcript)
    except OSError:
        verdict(False, f"transcript file not found or unreadable: {args.transcript}")
        sys.exit(1)

    quotes = [q for q in extract_quotes(entry.get("evidence")) if q and q.strip()]
    if not quotes:
        verdict(False, "evidence field is empty, nothing to verify")
        sys.exit(2)

    anchors = [{"quote": q[:120], "found": check_quote(q, hay)} for q in quotes]
    missing = [a for a in anchors if not a["found"]]
    if missing:
        verdict(False, f"anchor not found in transcript: '{missing[0]['quote']}'", anchors)
        sys.exit(2)

    verdict(True, "all evidence anchors confirmed in transcript", anchors)
    if not args.write:
        sys.exit(0)

    payload = {k: v for k, v in entry.items() if k not in SERVER_FIELDS}
    res = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / "log-learning.py")],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    sys.stdout.write(res.stdout)
    sys.stderr.write(res.stderr)
    sys.exit(0 if res.returncode == 0 else 1)


if __name__ == "__main__":
    main()
