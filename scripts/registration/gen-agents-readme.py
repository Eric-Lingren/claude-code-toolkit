#!/usr/bin/env python3
"""
gen-agents-readme.py -- Regenerate agents/README.md from agents/registry.json.

Lists every registered agent grouped by lane (first folder under agents/),
with model and consumers. Output is deterministic (sorted, no timestamps).

Usage:
  python3 gen-agents-readme.py              # write agents/README.md
  python3 gen-agents-readme.py --out /path  # write to a different file
"""

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]  # claude-code-shared/
REGISTRY = BASE / "agents" / "registry.json"
OUT_DEFAULT = BASE / "agents" / "README.md"


def lane_of(file_path: str) -> str:
    parts = file_path.split("/")
    return parts[1] if len(parts) > 2 else "(root)"


def render(agents: list) -> str:
    lanes = {}
    for a in agents:
        lanes.setdefault(lane_of(a["file"]), []).append(a)

    lines = [
        "# Agents",
        "",
        "Generated from `registry.json` by `scripts/registration/gen-agents-readme.py`. Do not edit by hand.",
        "",
        f"{len(agents)} agents across {len(lanes)} lanes.",
        "",
        "Skill-agents run the same-named skill in an isolated context window.",
        "",
    ]
    for lane in sorted(lanes):
        members = sorted(lanes[lane], key=lambda a: a["name"])
        lines += [
            f"## {lane}",
            "",
            "| Agent | Model | Consumers |",
            "|---|---|---|",
        ]
        for a in members:
            consumers = ", ".join(sorted(a["consumers"])) or "-"
            lines.append(f"| [{a['name']}]({a['file'].split('/', 1)[1]}) | {a['model']} | {consumers} |")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(OUT_DEFAULT))
    args = parser.parse_args()
    data = json.loads(REGISTRY.read_text())
    Path(args.out).write_text(render(data["agents"]))
    print(f"wrote {args.out} ({len(data['agents'])} agents)")


if __name__ == "__main__":
    main()
