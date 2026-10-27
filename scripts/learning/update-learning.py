#!/usr/bin/env python3
"""update-learning.py — update status and/or target of an existing learning entry.

Finds a learning entry in unified-learnings.jsonl by UUID, validates the new
values against learning-schema.json, then atomically rewrites the file.

Usage:
    python update-learning.py --id <uuid> [--status <s>] [--improves <slug>] [--improves-type <t>]

At least one of --status, --improves, --improves-type is required.
Retarget a misrouted learning: --improves <slug> --improves-type <t> --status captured

Valid status values: captured, applied, stale, invalid
Valid improves_type values: skill, agent, process, contract

Environment:
    LOG_LEARNING_DEST — override the learnings/ directory (used by tests).

Exit 0 on success. Exit 1 on any error (printed to stderr).
"""

import argparse
import fcntl
import json
import os
import pathlib
import sys
import tempfile


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONTRACTS_DIR = SCRIPT_DIR.parents[1] / "contracts"
SCHEMA_PATH = CONTRACTS_DIR / "learning-schema.json"

_dest_override = os.environ.get("LOG_LEARNING_DEST")
LEARNINGS_DIR = pathlib.Path(_dest_override) if _dest_override else SCRIPT_DIR.parents[1] / "learnings"


def _get_enum(field):
    """Extract allowed values for a schema property."""
    try:
        schema = json.loads(SCHEMA_PATH.read_text())
    except Exception as e:
        print(f"ERROR: could not load learning-schema.json: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        return schema["properties"][field]["enum"]
    except (KeyError, TypeError) as e:
        print(f"ERROR: could not read {field} enum from schema: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Update status and/or target of an existing learning entry."
    )
    parser.add_argument("--id", required=True, help="UUID of the learning entry to update.")
    parser.add_argument("--status", help="New status value.")
    parser.add_argument("--improves", help="New target slug (retarget).")
    parser.add_argument("--improves-type", dest="improves_type", help="New target type.")
    args = parser.parse_args()

    entry_id = args.id.strip()

    if not entry_id:
        print("ERROR: --id must be a non-empty string", file=sys.stderr)
        sys.exit(1)

    updates = {}
    for field, value in (
        ("status", args.status),
        ("improves", args.improves),
        ("improves_type", args.improves_type),
    ):
        if value is None:
            continue
        value = value.strip()
        if not value:
            print(f"ERROR: --{field.replace('_', '-')} must be non-empty", file=sys.stderr)
            sys.exit(1)
        if field != "improves":
            allowed = _get_enum(field)
            if value not in allowed:
                print(
                    f"ERROR: invalid {field} '{value}'. Must be one of: {', '.join(allowed)}",
                    file=sys.stderr,
                )
                sys.exit(1)
        updates[field] = value

    if not updates:
        print("ERROR: pass at least one of --status, --improves, --improves-type", file=sys.stderr)
        sys.exit(1)

    # Locate the JSONL file
    jsonl_path = LEARNINGS_DIR / "unified-learnings.jsonl"
    if not jsonl_path.exists():
        print(f"ERROR: learnings file not found: {jsonl_path}", file=sys.stderr)
        sys.exit(1)

    # Read + lock + find + rewrite atomically
    try:
        with open(jsonl_path, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                lines = fh.readlines()

                updated_lines = []
                found = False
                for line in lines:
                    stripped = line.rstrip("\n")
                    if not stripped:
                        updated_lines.append(line)
                        continue
                    try:
                        entry = json.loads(stripped)
                    except json.JSONDecodeError:
                        # Preserve malformed lines unchanged
                        updated_lines.append(line)
                        continue

                    if entry.get("id") == entry_id:
                        if found:
                            print(
                                f"ERROR: duplicate entry with id={entry_id}; file may be corrupt",
                                file=sys.stderr,
                            )
                            sys.exit(1)
                        entry.update(updates)
                        found = True
                        updated_lines.append(
                            json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
                        )
                    else:
                        updated_lines.append(line)

                if not found:
                    print(
                        f"ERROR: no entry with id={entry_id} found in {jsonl_path}",
                        file=sys.stderr,
                    )
                    sys.exit(1)

                # Write to a temp file in the same directory, then replace
                tmp_fd, tmp_path = tempfile.mkstemp(
                    dir=LEARNINGS_DIR, prefix=".update-learning-tmp-", suffix=".jsonl"
                )
                try:
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_fh:
                        tmp_fh.writelines(updated_lines)
                        tmp_fh.flush()
                        os.fsync(tmp_fh.fileno())
                    os.replace(tmp_path, jsonl_path)
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise

            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    except OSError as e:
        print(f"ERROR: could not update {jsonl_path}: {e}", file=sys.stderr)
        sys.exit(1)

    changes = ", ".join(f"{k} -> {v}" for k, v in updates.items())
    print(f"OK: updated entry id={entry_id} {changes} in {jsonl_path}")


if __name__ == "__main__":
    main()
