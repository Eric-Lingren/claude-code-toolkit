#!/usr/bin/env python3
"""contract_sources.py <agent> — print the numbered source bundle the contract drafter cites.

Three sources, every line prefixed with its 1-based line number so citations are exact:
  role         agents/**/<agent>.md
  caller       each consumer in agents/registry.json (skills/<c>/SKILL.md or agents/**/<c>.md);
               when the registry lists none, files that mention the agent name are used.
               Only lines near a mention are printed for callers.
  side_effect  cited from the role/caller lines that name files written or tool calls required.
Plain text for the Opus drafter. Zero tokens to produce.
"""
import glob
import os
import re
import sys

import bench_lib as L

CTX = 6


def find_agent_file(name):
    hits = glob.glob(os.path.join(L.SHARED_DIR, "agents", "**", name + ".md"), recursive=True)
    return hits[0] if hits else None


def read_lines(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def numbered(path, only=None):
    out = []
    for i, ln in enumerate(read_lines(path), 1):
        if only is None or i in only:
            out.append(f"{i:>4}: {ln}")
        elif out and out[-1] != "  ...":
            out.append("  ...")
    return "\n".join(out)


def mention_lines(path, name):
    lines = read_lines(path)
    keep = set()
    for i, ln in enumerate(lines, 1):
        if name in ln:
            keep.update(range(max(1, i - CTX), min(len(lines), i + CTX) + 1))
    return keep


def consumer_files(agent):
    reg = L.read_json(os.path.join(L.SHARED_DIR, "agents", "registry.json"), {"agents": []})
    listed = []
    for a in reg.get("agents", []):
        if a["name"] == agent:
            listed = a.get("consumers", [])
    files = []
    for c in listed:
        for pat in (f"skills/{c}/SKILL.md", f"agents/**/{c}.md"):
            files += glob.glob(os.path.join(L.SHARED_DIR, pat), recursive=True)
    mode = "registry"
    if not files:
        mode = "grep-fallback (agent absent from registry.json or lists no consumers)"
        me = find_agent_file(agent)
        rx = re.compile(r"(?<![\w-])" + re.escape(agent) + r"(?![\w-])")
        for pat in ("skills/*/SKILL.md", "agents/**/*.md", "resources/*.md"):
            for f in glob.glob(os.path.join(L.SHARED_DIR, pat), recursive=True):
                if f != me and rx.search("\n".join(read_lines(f))):
                    files.append(f)
    return sorted(set(files)), mode, listed


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    agent = sys.argv[1]
    me = find_agent_file(agent)
    if not me:
        sys.exit(f"no agents/**/{agent}.md")

    def rel(p):
        return os.path.relpath(p, L.SHARED_DIR)

    print(f"# SOURCES FOR {agent}\ncitation file paths are relative to claude-code-shared/\n")
    print(f"## source tag: role  file: {rel(me)}\n{numbered(me)}\n")
    files, mode, listed = consumer_files(agent)
    print(f"## source tag: caller  consumers via {mode}; registry consumers: {listed}")
    if not files:
        print("(no consumer files found)")
    for f in files:
        keep = mention_lines(f, agent) or None
        print(f"\n### caller file: {rel(f)}\n{numbered(f, keep)}")
    print("\n## source tag: side_effect\nCite lines from the role file or caller files above that name files written or "
          "tool calls required (Write/Edit targets, Bash commands such as log-*.py, MCP writes) with tag side_effect.")


if __name__ == "__main__":
    main()
