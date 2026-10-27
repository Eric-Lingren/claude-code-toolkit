#!/usr/bin/env python3
"""log-learning.py — validate and atomically append a v2 learning entry to unified-learnings.jsonl.

Reads a partial learning entry as JSON from stdin. Rejects any caller-provided
server-injected fields (schema_version, id, timestamp), injects them, validates
against learning-schema.json, then atomically appends to
claude-code-shared/learnings/unified-learnings.jsonl.

Usage:
    echo '{...}' | python log-learning.py

Caller supplies all fields EXCEPT: schema_version, id, timestamp.

Required caller fields (see learning-schema.json for full contract):
    type, reported_by, improves_type, cause, problem, why_missed, lesson,
    fix, evidence, confidence
    Plus: improves (nullable), cause_label (null unless cause=='other'),
    and optionally trace (attribution records) and status.

Environment:
    LOG_LEARNING_DEST — override the learnings/ directory (used by tests).

Exit 0 on success. Exit 1 on any error (printed to stderr).
"""

import fcntl
import json
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import datetime, timezone


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
CONTRACTS_DIR = SCRIPT_DIR.parents[1] / "contracts"
SCHEMA_PATH = CONTRACTS_DIR / "learning-schema.json"

_dest_override = os.environ.get("LOG_LEARNING_DEST")
LEARNINGS_DIR = pathlib.Path(_dest_override) if _dest_override else SCRIPT_DIR.parents[1] / "learnings"

SERVER_FIELDS = {"schema_version", "id", "timestamp"}


def _build_validator(schema):
    import re
    try:
        from importlib.metadata import version as pkg_version
        v = pkg_version("jsonschema")
    except Exception:
        import jsonschema
        v = jsonschema.__version__
    m = re.match(r"(\d+)\.(\d+)", v)
    major, minor = (int(m.group(1)), int(m.group(2))) if m else (0, 0)

    use_registry = (major > 4) or (major == 4 and minor >= 18)
    if use_registry:
        try:
            from referencing import Registry, Resource
            from referencing.jsonschema import DRAFT7
            from jsonschema import Draft7Validator

            all_schemas = {}
            for sf in sorted(CONTRACTS_DIR.glob("*.json")):
                try:
                    data = json.loads(sf.read_text())
                    if "$id" in data:
                        all_schemas[data["$id"]] = data
                except Exception:
                    pass

            resources = [
                (sid, Resource.from_contents(sdata, default_specification=DRAFT7))
                for sid, sdata in all_schemas.items()
            ]
            registry = Registry().with_resources(resources)
            return Draft7Validator(schema, registry=registry)
        except ImportError:
            pass

    from jsonschema import Draft7Validator, RefResolver
    return Draft7Validator(schema, resolver=RefResolver(
        base_uri=schema.get("$id", ""), referrer=schema, store={}
    ))


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        print("ERROR: no input on stdin", file=sys.stderr)
        sys.exit(1)

    try:
        entry = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: stdin is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(entry, dict):
        print("ERROR: input must be a JSON object", file=sys.stderr)
        sys.exit(1)

    # Reject caller-provided server-injected fields
    caller_server_fields = SERVER_FIELDS & entry.keys()
    if caller_server_fields:
        bad = ", ".join(sorted(caller_server_fields))
        print(
            f"ERROR: caller must not supply server-injected fields: {bad}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Inject server-side fields
    entry["schema_version"] = "2"
    entry["id"] = str(uuid.uuid4())
    entry["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Load schema
    try:
        schema = json.loads(SCHEMA_PATH.read_text())
    except Exception as e:
        print(f"ERROR: could not load learning-schema.json: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate
    try:
        validator = _build_validator(schema)
        errors = sorted(validator.iter_errors(entry), key=lambda e: list(e.absolute_path))
    except Exception as e:
        print(f"ERROR: validator setup failed: {e}", file=sys.stderr)
        sys.exit(1)

    if errors:
        for err in errors:
            path = ".".join(str(p) for p in err.absolute_path) or "(root)"
            print(f"ERROR: validation failed at {path}: {err.message}", file=sys.stderr)
        sys.exit(1)

    # Atomic append: write to temp file then rename (append semantics via lock)
    LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LEARNINGS_DIR / "unified-learnings.jsonl"
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"

    try:
        with open(out_path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError as e:
        print(f"ERROR: could not write to {out_path}: {e}", file=sys.stderr)
        sys.exit(1)

    lesson = " ".join(str(entry.get("lesson", "")).split())
    print(f"OK: appended learning entry (type={entry['type']}, id={entry['id']}) to {out_path} | lesson: {lesson}")


if __name__ == "__main__":
    main()
