#!/usr/bin/env python3
"""update-learning.py — update status and/or target of an existing learning entry.

Finds a learning entry in unified-learnings.jsonl by UUID, validates the new
values against learning-schema.json, then atomically rewrites the file.

Usage:
    python update-learning.py --id <uuid> [--status <s>] [--improves <slug>] [--improves-type <t>]
                              [--merged-sha <sha>] [--reason <text>] [--escape-point <json>]

At least one of --status, --improves, --improves-type, --merged-sha, --reason, --escape-point is required.
--escape-point takes a JSON object {"path": ..., "section": ...} (validated by the schema).
Retarget a misrouted learning: --improves <slug> --improves-type <t> --status captured
Record a merged fix: --status applied --merged-sha <sha>
Reject a learning (never re-proposed): --status rejected --reason "<user's close comment>"
Cluster a learning: --status clustered:<issue#>

Valid status values: captured, applied, stale, invalid, rejected, clustered:<issue#>
Valid improves_type values: skill, agent, process, contract

The updated entry is validated against learning-schema.json before anything is written.
A status of rejected requires a reason (on the entry already, or passed via --reason).

Environment:
    LOG_LEARNING_DEST — override the learnings/ directory (used by tests).

Exit 0 on success. Exit 1 on any error (printed to stderr).
"""

import argparse
import fcntl
import json
import os
import pathlib
import re
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


def _validate_entry(entry):
    """Validate a full entry against learning-schema.json; return list of error strings."""
    try:
        from jsonschema import Draft7Validator
    except ImportError as e:
        print(f"ERROR: jsonschema is required to validate updates: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        schema = json.loads(SCHEMA_PATH.read_text())
    except Exception as e:
        print(f"ERROR: could not load learning-schema.json: {e}", file=sys.stderr)
        sys.exit(1)
    errors = []
    for err in sorted(Draft7Validator(schema).iter_errors(entry), key=lambda e: list(e.path)):
        where = ".".join(str(p) for p in err.path) or "(entry)"
        errors.append(f"{where}: {err.message}")
    return errors


def main():
    parser = argparse.ArgumentParser(
        description="Update status and/or target of an existing learning entry."
    )
    parser.add_argument("--id", required=True, help="UUID of the learning entry to update.")
    parser.add_argument("--status", help="New status value.")
    parser.add_argument("--improves", help="New target slug (retarget).")
    parser.add_argument("--improves-type", dest="improves_type", help="New target type.")
    parser.add_argument("--merged-sha", dest="merged_sha", help="Commit SHA at which the fix merged.")
    parser.add_argument("--reason", help="Why the learning was rejected (the user's close comment).")
    parser.add_argument("--escape-point", dest="escape_point",
                        help='JSON object {"path": "...", "section": "..."|null}.')
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
        ("merged_sha", args.merged_sha),
        ("reason", args.reason),
    ):
        if value is None:
            continue
        value = value.strip()
        if not value:
            print(f"ERROR: --{field.replace('_', '-')} must be non-empty", file=sys.stderr)
            sys.exit(1)
        if field == "improves" and (not re.match(r"^[a-z0-9][a-z0-9._-]*$", value) or ".." in value):
            print(f"ERROR: invalid improves '{value}'. Must match ^[a-z0-9][a-z0-9._-]*$ and not contain '..'", file=sys.stderr)
            sys.exit(1)
        if field == "improves_type":
            allowed = _get_enum(field)
            if value not in allowed:
                print(
                    f"ERROR: invalid {field} '{value}'. Must be one of: {', '.join(allowed)}",
                    file=sys.stderr,
                )
                sys.exit(1)
        updates[field] = value

    if args.escape_point is not None:
        try:
            updates["escape_point"] = json.loads(args.escape_point)
        except json.JSONDecodeError as e:
            print(f"ERROR: --escape-point is not valid JSON: {e}", file=sys.stderr)
            sys.exit(1)

    if not updates:
        print("ERROR: pass at least one of --status, --improves, --improves-type, --merged-sha, --reason, --escape-point", file=sys.stderr)
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
                        problems = _validate_entry(entry)
                        if problems:
                            print(
                                f"ERROR: update would make entry id={entry_id} invalid "
                                f"against learning-schema.json (nothing written):\n  "
                                + "\n  ".join(problems),
                                file=sys.stderr,
                            )
                            sys.exit(1)
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
