#!/usr/bin/env python3
"""Create a validated task file envelope from metadata + tasks JSON.

Usage:
  create-task-envelope.py --producer <name> --source-type <type> \
    [--source-ref <ref>] --strategy <single|per-task> [--branch <name>] \
    [--local-only] --tasks-file <path> [--follow-ups-file <path>] \
    --output <path>

  --producer        Skill that generated this file (e.g. to-tasks, pr-code-review)
  --source-type     One of: seed, prd, session
  --source-ref      Basename of source artifact (null if omitted)
  --strategy        Branching strategy: single or per-task
  --branch          Branch name (required when strategy is single)
  --local-only      Set local_only: true (skip push/PR)
  --tasks-file      Path to JSON file containing the tasks array
  --follow-ups-file Path to JSON file containing the follow_ups array (defaults to [])
  --output          Output path for the task file

Writes a schema-valid task file envelope. Runs validate-schema.sh after writing.
Exit 0 on success, non-zero on validation failure.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description="Create a task file envelope")
    parser.add_argument("--producer", required=True)
    parser.add_argument("--source-type", required=True, choices=["seed", "prd", "session"])
    parser.add_argument("--source-ref", default=None)
    parser.add_argument("--strategy", required=True, choices=["single", "per-task"])
    parser.add_argument("--branch", default=None)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--tasks-file", required=True)
    parser.add_argument("--follow-ups-file", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.strategy == "single" and not args.branch:
        print("ERROR: --branch required when strategy is single", file=sys.stderr)
        sys.exit(1)

    with open(args.tasks_file) as f:
        tasks = json.load(f)

    follow_ups = []
    if args.follow_ups_file:
        with open(args.follow_ups_file) as f:
            follow_ups = json.load(f)

    branching = {"strategy": args.strategy}
    if args.branch:
        branching["branch"] = args.branch

    envelope = {
        "schema_version": "2",
        "producer": args.producer,
        "source": {
            "type": args.source_type,
            "ref": args.source_ref,
        },
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "branching": branching,
        "tasks": tasks,
        "follow_ups": follow_ups,
    }

    if args.local_only:
        envelope["local_only"] = True

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(envelope, f, indent=2)
        f.write("\n")

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    schema_path = os.path.join(scripts_dir, "..", "..", "contracts", "task-schema.json")
    result = subprocess.run(
        ["bash", os.path.join(scripts_dir, "..", "shared", "validate-schema.sh"),
         "--instance", schema_path, args.output],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(result.returncode)

    print(f"OK: {args.output}")


if __name__ == "__main__":
    main()
