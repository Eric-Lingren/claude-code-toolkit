#!/usr/bin/env python3
"""Validate agents/registry.json against the agents/ directory.

Exit 0: registry is consistent.
Exit 1: errors found (printed to stdout).

Usage:
  python3 validate-registry.py [--base-dir PATH]
  Default base-dir: parent of this script (claude-code-shared/).
"""
import argparse
import glob
import json
import os
import sys

REQUIRED_AGENT_FIELDS = {"name", "file", "model", "description", "consumers"}


def validate(agents_dir: str, registry_path: str) -> tuple:
    """Return (ok: bool, errors: list[str])."""
    errors = []

    if not os.path.isfile(registry_path):
        return False, [f"registry not found: {registry_path}"]

    try:
        with open(registry_path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"malformed JSON in registry: {e}"]

    if "agents" not in data:
        return False, ["registry missing top-level 'agents' key"]

    registered_names = set()

    for entry in data["agents"]:
        name = entry.get("name", "<unnamed>")
        registered_names.add(name)

        missing_fields = REQUIRED_AGENT_FIELDS - set(entry.keys())
        if missing_fields:
            errors.append(
                f"agent '{name}' missing required fields: {sorted(missing_fields)}"
            )
            continue

        agent_file = os.path.join(os.path.dirname(agents_dir), entry["file"])
        if not os.path.isfile(agent_file):
            errors.append(
                f"agent '{name}' registered but file not found: {agent_file}"
            )

    for md_path in glob.glob(os.path.join(agents_dir, "**", "*.md"), recursive=True):
        stem = os.path.splitext(os.path.basename(md_path))[0]
        if stem in ("registry", "README"):
            continue
        if stem not in registered_names:
            errors.append(
                f"orphaned agent file with no registry entry: {os.path.relpath(md_path, agents_dir)}"
            )

    return len(errors) == 0, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        help="path to claude-code-shared/ root (default: parent of this script)",
    )
    args = parser.parse_args()

    agents_dir = os.path.join(args.base_dir, "agents")
    registry_path = os.path.join(agents_dir, "registry.json")

    ok, errors = validate(agents_dir, registry_path)
    if ok:
        print("registry OK")
    else:
        for e in errors:
            print(f"ERROR: {e}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
