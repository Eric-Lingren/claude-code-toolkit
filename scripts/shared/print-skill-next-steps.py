#!/usr/bin/env python3
"""Print next-step suggestions for a skill from skill-pipeline.json.

Usage: skill-next-steps.py <skill-slug>
Output: one line per downstream edge, e.g. "  /to-tasks — ready to implement without a formal PRD"
Exit 0 always; prints nothing if the skill has no next edges.
"""
import json
import sys
from pathlib import Path

PIPELINE = Path(__file__).parents[2] / "skill-pipeline.json"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: skill-next-steps.py <skill-slug>", file=sys.stderr)
        sys.exit(1)

    slug = sys.argv[1]
    data = json.loads(PIPELINE.read_text())
    skill = data.get("skills", {}).get(slug)

    if skill is None:
        print(f"skill '{slug}' not in pipeline", file=sys.stderr)
        sys.exit(1)

    for edge in skill.get("next", []):
        print(f"  /{edge['skill']} — {edge['when']}")


if __name__ == "__main__":
    main()
