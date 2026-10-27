#!/usr/bin/env python3
"""filter-session-transcript.py — clean a Claude Code session JSONL for persona use.

Drops thinking/signature content blocks and metadata entry types.
Caps tool_result blocks at ~2KB. Writes cleaned JSONL to output path.

Usage:
  python3 filter-session-transcript.py <output-path>

Environment:
  transcript_path         Explicit path to session JSONL (overrides auto-resolve)
  CLAUDE_CODE_SESSION_ID  Session ID for auto-resolve (combined with CLAUDE_CONFIG_DIR)
  CLAUDE_CONFIG_DIR       Claude config dir (default: ~/.claude)

Exit codes:
  0  Success — cleaned file written to output-path
  1  Error — session JSONL not found, unresolvable path, or parse failure
"""

import sys
import json
import os
import pathlib

TOOL_RESULT_CAP = 2048  # bytes

METADATA_ENTRY_TYPES = frozenset({
    "last-prompt", "mode", "permission-mode", "attachment",
    "file-history-snapshot", "ai-title", "system",
})

DROP_CONTENT_BLOCK_TYPES = frozenset({"thinking", "signature"})


def encode_cwd(path: str) -> str:
    """Encode a cwd path to the project directory name Claude Code uses.

    Claude Code replaces each '/' and '.' with '-' in the project directory name.
    Example: /Users/eric/.dotfiles -> -Users-eric--dotfiles
    """
    result = []
    for ch in path:
        if ch in (".", "/"):
            result.append("-")
        else:
            result.append(ch)
    return "".join(result)


def resolve_session_path() -> pathlib.Path:
    """Resolve the session JSONL path from environment variables."""
    transcript_path = os.environ.get("transcript_path")
    if transcript_path:
        p = pathlib.Path(transcript_path)
        if not p.exists():
            print(
                f"ERROR: transcript_path env var set but file not found: {transcript_path}",
                file=sys.stderr,
            )
            sys.exit(1)
        return p

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        print(
            "ERROR: CLAUDE_CODE_SESSION_ID not set and transcript_path not provided.\n"
            "  Set transcript_path=<path> or CLAUDE_CODE_SESSION_ID + CLAUDE_CONFIG_DIR.",
            file=sys.stderr,
        )
        sys.exit(1)

    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    cwd = os.getcwd()
    encoded = encode_cwd(cwd)

    p = pathlib.Path(config_dir) / "projects" / encoded / f"{session_id}.jsonl"
    if not p.exists():
        print(
            f"ERROR: Session JSONL not found: {p}\n"
            f"  CLAUDE_CODE_SESSION_ID={session_id}\n"
            f"  CLAUDE_CONFIG_DIR={config_dir}\n"
            f"  Encoded cwd: {encoded}",
            file=sys.stderr,
        )
        sys.exit(1)
    return p


def cap_text(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= TOOL_RESULT_CAP:
        return text
    truncated = encoded[:TOOL_RESULT_CAP].decode("utf-8", errors="replace")
    return truncated + f"\n[...truncated at {TOOL_RESULT_CAP}B]"


def filter_tool_result_content(content):
    """Cap tool_result content at TOOL_RESULT_CAP bytes."""
    if isinstance(content, str):
        return cap_text(content)
    if isinstance(content, list):
        out = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                capped = cap_text(text)
                if capped is not text:
                    block = dict(block)
                    block["text"] = capped
            out.append(block)
        return out
    return content


def filter_content_blocks(blocks):
    """Strip thinking/signature blocks; cap tool_result content."""
    if not isinstance(blocks, list):
        return blocks
    out = []
    for block in blocks:
        if not isinstance(block, dict):
            out.append(block)
            continue
        btype = block.get("type", "")
        if btype in DROP_CONTENT_BLOCK_TYPES:
            continue
        if btype == "tool_result":
            raw = block.get("content")
            if raw is not None:
                capped = filter_tool_result_content(raw)
                if capped is not raw:
                    block = dict(block)
                    block["content"] = capped
        out.append(block)
    return out


def filter_entry(obj: dict):
    """Filter a single JSONL entry. Returns None to drop the line."""
    entry_type = obj.get("type", "")
    if entry_type in METADATA_ENTRY_TYPES:
        return None
    if entry_type in ("user", "assistant"):
        message = obj.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                filtered = filter_content_blocks(content)
                if filtered is not content:
                    obj = dict(obj)
                    obj["message"] = dict(message)
                    obj["message"]["content"] = filtered
    return obj


def main():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    if not args:
        print(
            "ERROR: output path required as first argument.\n"
            "Usage: filter-session-transcript.sh <output-path>",
            file=sys.stderr,
        )
        sys.exit(1)

    output_path = pathlib.Path(args[0])
    session_path = resolve_session_path()

    lines_in = 0
    lines_out = 0

    with session_path.open(encoding="utf-8", errors="replace") as fin, \
         output_path.open("w", encoding="utf-8") as fout:
        for raw_line in fin:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            lines_in += 1
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError as e:
                print(f"WARNING: skipping malformed JSONL line {lines_in}: {e}", file=sys.stderr)
                continue
            filtered = filter_entry(obj)
            if filtered is not None:
                fout.write(json.dumps(filtered, ensure_ascii=False) + "\n")
                lines_out += 1

    in_size = session_path.stat().st_size
    out_size = output_path.stat().st_size
    reduction = (1 - out_size / in_size) * 100 if in_size > 0 else 0.0

    print(
        f"OK: {lines_in} lines in, {lines_out} lines out, "
        f"{in_size:,}B -> {out_size:,}B ({reduction:.0f}% reduction)"
    )


if __name__ == "__main__":
    main()
