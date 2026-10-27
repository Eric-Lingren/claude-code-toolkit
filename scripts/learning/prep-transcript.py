#!/usr/bin/env python3
"""prep-transcript.py — render a Claude Code session JSONL as greppable plain text.

One output line per content block, prefixed with its source JSONL line number:

  [L412] user: the actual text...
  [L413] assistant: text...
  [L413] assistant/tool_use Bash: {"command": "..."}
  [L414] user/tool_result: error text...

Newlines inside a block are collapsed to spaces so each block greps as one line
and quotes copied from here are verbatim (no JSON escapes). Thinking/signature
blocks and metadata entry types are dropped. tool_result and tool_use input are
capped so a single huge tool output can't dominate the file.

Usage:
  prep-transcript.py [--transcript PATH] [--out PATH]

  --transcript  session JSONL (default: resolved via resolve-transcript-path.sh)
  --out         output path (default: $TMPDIR/cc-transcript-<session-id>.txt)

Prints: "<out-path> <line-count>" on success.

Exit codes:
  0  success
  1  transcript not found / unresolvable
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
TOOL_RESULT_CAP = 4000
TOOL_INPUT_CAP = 1000

METADATA_ENTRY_TYPES = frozenset({
    "last-prompt", "mode", "permission-mode", "attachment",
    "file-history-snapshot", "ai-title", "system", "summary",
})
DROP_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking", "signature"})


def flat(text, cap=None):
    text = " ".join(str(text).split())
    if cap and len(text) > cap:
        text = text[:cap] + f" …[truncated {len(text) - cap} chars]"
    return text


def tool_result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def render_entry(lineno, entry, capped=True):
    if entry.get("type") in METADATA_ENTRY_TYPES:
        return []
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return []
    role = msg.get("role") or entry.get("type") or "?"
    content = msg.get("content")
    out = []
    if isinstance(content, str):
        if content.strip():
            out.append(f"[L{lineno}] {role}: {flat(content)}")
        return out
    if not isinstance(content, list):
        return out
    for b in content:
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        if kind in DROP_BLOCK_TYPES:
            continue
        if kind == "text" and b.get("text", "").strip():
            out.append(f"[L{lineno}] {role}: {flat(b['text'])}")
        elif kind == "tool_use":
            inp = json.dumps(b.get("input", {}), ensure_ascii=False)
            out.append(f"[L{lineno}] {role}/tool_use {b.get('name', '?')}: {flat(inp, TOOL_INPUT_CAP if capped else None)}")
        elif kind == "tool_result":
            text = tool_result_text(b.get("content"))
            if text.strip():
                out.append(f"[L{lineno}] {role}/tool_result: {flat(text, TOOL_RESULT_CAP if capped else None)}")
    return out


def render(path, capped=True):
    """Return the rendered plain-text lines for a session JSONL.

    capped=False keeps full tool inputs/results (used by verify-anchors.py).
    """
    lines = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            lines.extend(render_entry(n, entry, capped))
    return lines


def resolve_transcript():
    try:
        res = subprocess.run(
            ["bash", str(SCRIPT_DIR / "resolve-transcript-path.sh")],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return None
    path = res.stdout.strip()
    return None if path in ("", "null") else path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript")
    ap.add_argument("--out")
    args = ap.parse_args()

    transcript = args.transcript or resolve_transcript()
    if not transcript or not os.path.isfile(transcript):
        print(f"ERROR: transcript not found: {transcript}", file=sys.stderr)
        sys.exit(1)

    out = args.out or os.path.join(
        tempfile.gettempdir(), f"cc-transcript-{pathlib.Path(transcript).stem}.txt"
    )
    lines = render(transcript)
    pathlib.Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{out} {len(lines)}")


if __name__ == "__main__":
    main()
