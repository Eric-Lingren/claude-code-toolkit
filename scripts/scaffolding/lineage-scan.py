#!/usr/bin/env python3
"""
lineage-scan.py — batch chain-traversal scanner for docs/ scaffolding artifacts.

Reads all JSON and MD files in docs/seeds/, docs/prd/, docs/tasks/, docs/handoffs/,
builds a lineage chain graph by tracing source.ref fields, and returns categorized
results as structured JSON to stdout.

Usage:
    python3 lineage-scan.py [--root <path>]

Exits 0 always. Missing directories produce empty results, not an error.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Scan docs/ lineage chains.")
    parser.add_argument(
        "--root",
        default=os.getcwd(),
        help="Project root directory (default: cwd)",
    )
    return parser.parse_args()


def read_json_files(directory: Path) -> dict:
    """Return {basename: parsed_dict} for all readable JSON files in directory."""
    results = {}
    if not directory.is_dir():
        return results
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results[path.name] = data
        except Exception:
            pass
    return results


def read_md_files(directory: Path) -> dict:
    """Return {basename: raw_text} for all readable MD files in directory."""
    results = {}
    if not directory.is_dir():
        return results
    for path in sorted(directory.glob("*.md")):
        try:
            results[path.name] = path.read_text(encoding="utf-8")
        except Exception:
            pass
    return results


def extract_source_ref(data: dict) -> str | None:
    """Extract source.ref from a parsed JSON artifact dict."""
    src = data.get("source")
    if not src or not isinstance(src, dict):
        return None
    src_type = src.get("type", "")
    if src_type == "session":
        return None
    return src.get("ref") or None


def extract_handoff_source_ref(text: str) -> str | None:
    """Extract seed basename from a handoff MD header line like '**Source ref:** `20260606-foo.json`'."""
    match = re.search(r"\*\*Source ref:\*\*\s*`([^`]+)`", text)
    if match:
        return match.group(1)
    return None


def task_file_is_consumed(data: dict) -> bool:
    """Return True if every task in a task JSON file has status done or merged."""
    tasks = data.get("tasks") or []
    if not tasks:
        # No tasks array — treat as consumed (e.g. meta-only file)
        return True
    active_statuses = {"not_started", "in_progress", "blocked"}
    for task in tasks:
        if isinstance(task, dict) and task.get("status", "") in active_statuses:
            return False
    return True


def build_chains(seeds, prds, tasks_files, handoffs_md):
    """
    Build lineage chains from the loaded artifact maps.

    Returns (chains, orphan_seeds, orphan_tasks, orphan_handoffs).

    A chain groups:
      seed basename -> {prd, tasks, handoff, status}

    Orphans are artifacts whose source.ref points to a seed not in seeds.
    """
    # Maps: child_basename -> parent_seed_basename
    child_to_seed: dict[str, str] = {}

    for basename, data in prds.items():
        ref = extract_source_ref(data)
        if ref and ref in seeds:
            child_to_seed[basename] = ref

    for basename, data in tasks_files.items():
        ref = extract_source_ref(data)
        if ref and ref in seeds:
            child_to_seed[basename] = ref

    for basename, text in handoffs_md.items():
        ref = extract_handoff_source_ref(text)
        if ref and ref in seeds:
            child_to_seed[basename] = ref

    # Build chain dict keyed by seed basename
    chain_map: dict[str, dict] = {}
    for seed_basename in seeds:
        chain_map[seed_basename] = {
            "seed": seed_basename,
            "prd": None,
            "tasks": None,
            "handoff": None,
            "status": "partial",
        }

    for prd_basename, seed_basename in child_to_seed.items():
        if prd_basename in prds:
            chain_map[seed_basename]["prd"] = prd_basename

    for tasks_basename, seed_basename in child_to_seed.items():
        if tasks_basename in tasks_files:
            chain_map[seed_basename]["tasks"] = tasks_basename

    for handoff_basename, seed_basename in child_to_seed.items():
        if handoff_basename in handoffs_md:
            chain_map[seed_basename]["handoff"] = handoff_basename

    # Determine status for each chain
    for seed_basename, chain in chain_map.items():
        tasks_ref = chain["tasks"]
        if tasks_ref:
            data = tasks_files[tasks_ref]
            consumed = task_file_is_consumed(data)
            chain["status"] = "complete" if consumed else "partial"
        else:
            # No tasks file yet -> partial
            chain["status"] = "partial"

    chains = list(chain_map.values())

    # Orphans: artifacts referencing a seed not present
    orphan_seeds: list[str] = []
    # Seeds that have no children at all and were never referenced are still in chains, not orphans.
    # Orphan tasks/handoffs are ones whose source.ref doesn't match any seed.
    orphan_tasks = []
    for basename, data in tasks_files.items():
        ref = extract_source_ref(data)
        if ref is not None and ref not in seeds:
            orphan_tasks.append(basename)
        elif ref is None and basename not in child_to_seed:
            # No source.ref — standalone orphan task
            orphan_tasks.append(basename)

    orphan_handoffs = []
    for basename, text in handoffs_md.items():
        ref = extract_handoff_source_ref(text)
        if ref is not None and ref not in seeds:
            orphan_handoffs.append(basename)
        elif ref is None and basename not in child_to_seed:
            orphan_handoffs.append(basename)

    return chains, orphan_seeds, orphan_tasks, orphan_handoffs


def main():
    args = parse_args()
    root = Path(args.root).resolve()
    docs = root / "docs"

    seeds = read_json_files(docs / "seeds")
    prds = read_json_files(docs / "prd")
    tasks_files = read_json_files(docs / "tasks")
    handoffs_md = read_md_files(docs / "handoffs")

    chains, orphan_seeds, orphan_tasks, orphan_handoffs = build_chains(
        seeds, prds, tasks_files, handoffs_md
    )

    complete_chains = sum(1 for c in chains if c["status"] == "complete")
    partial_chains = sum(1 for c in chains if c["status"] == "partial")

    output = {
        "chains": chains,
        "orphans": {
            "seeds": orphan_seeds,
            "tasks": orphan_tasks,
            "handoffs": orphan_handoffs,
        },
        "stats": {
            "total_seeds": len(seeds),
            "total_tasks": len(tasks_files),
            "complete_chains": complete_chains,
            "partial_chains": partial_chains,
        },
    }

    print(json.dumps(output, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
