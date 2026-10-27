#!/usr/bin/env python3
"""Upsert an agent entry into the agents/registry.json consumer map.

Usage:
  python3 registry-sync.py <registry-path> <entry-json>

Where <entry-json> is a JSON string with keys: name, file, model, description, consumers.
"""
import copy
import json
import sys


def upsert_agent(registry: dict, entry: dict) -> dict:
    """Add or update an agent entry in the registry.

    Does not mutate the original registry dict. Returns a new dict.
    """
    updated = copy.deepcopy(registry)
    agents = updated.setdefault("agents", [])
    name = entry["name"]

    for i, existing in enumerate(agents):
        if existing.get("name") == name:
            agents[i] = copy.deepcopy(entry)
            return updated

    agents.append(copy.deepcopy(entry))
    return updated


def main():
    if len(sys.argv) < 3:
        print("Usage: registry-sync.py <registry-path> <entry-json>", file=sys.stderr)
        sys.exit(1)

    registry_path = sys.argv[1]
    entry = json.loads(sys.argv[2])

    with open(registry_path) as f:
        registry = json.load(f)

    updated = upsert_agent(registry, entry)

    with open(registry_path, "w") as f:
        json.dump(updated, f, indent=2)
        f.write("\n")

    print(f"registry updated: {entry['name']}")


if __name__ == "__main__":
    main()
